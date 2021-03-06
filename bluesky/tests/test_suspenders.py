import pytest
import ophyd
import asyncio
from functools import partial
from bluesky.suspenders import (SuspendBoolHigh,
                                SuspendBoolLow,
                                SuspendFloor,
                                SuspendCeil,
                                SuspendInBand,
                                SuspendOutBand)
from bluesky.tests.utils import MsgCollector
from bluesky import Msg
import time as ttime
from bluesky.run_engine import RunEngineInterrupted
import time

@pytest.mark.parametrize(
    'klass,sc_args,start_val,fail_val,resume_val,wait_time',
    [(SuspendBoolHigh, (), 0, 1, 0, .2),
     (SuspendBoolLow, (), 1, 0, 1, .2),
     (SuspendFloor, (.5,), 1, 0, 1, .2),
     (SuspendCeil, (.5,), 0, 1, 0, .2),
     (SuspendInBand, (.5, 1.5), 1, 0, 1, .2),
     (SuspendOutBand, (.5, 1.5), 0, 1, 0, .2)])
def test_suspender(klass, sc_args, start_val, fail_val,
                   resume_val, wait_time, fresh_RE):
    RE = fresh_RE
    loop = RE._loop
    sig = ophyd.Signal()
    my_suspender = klass(sig,
                         *sc_args, sleep=wait_time)
    my_suspender.install(RE)

    def putter(val):
        sig.put(val)

    # make sure we start at good value!
    putter(start_val)
    # dumb scan
    scan = [Msg('checkpoint'), Msg('sleep', None, .2)]
    RE(scan)
    # paranoid
    assert RE.state == 'idle'

    start = ttime.time()
    # queue up fail and resume conditions
    loop.call_later(.1, putter, fail_val)
    loop.call_later(.5, putter, resume_val)
    # start the scan
    RE(scan)
    stop = ttime.time()
    # assert we waited at least 2 seconds + the settle time
    delta = stop - start
    print(delta)
    assert delta > .5 + wait_time + .2


def test_pretripped(fresh_RE):
    'Tests if suspender is tripped before __call__'
    RE = fresh_RE
    sig = ophyd.Signal()
    scan = [Msg('checkpoint')]
    msg_lst = []
    sig.put(1)

    def accum(msg):
        msg_lst.append(msg)

    susp = SuspendBoolHigh(sig)

    RE.install_suspender(susp)
    RE._loop.call_later(1, sig.put, 0)
    RE.msg_hook = accum
    RE(scan)

    assert len(msg_lst) == 2
    assert ['wait_for', 'checkpoint'] == [m[0] for m in msg_lst]


@pytest.mark.parametrize('pre_plan,post_plan,expected_list',
                         [([Msg('null')], None,
                           ['checkpoint', 'sleep', 'rewindable', 'null',
                            'wait_for', 'rewindable', 'sleep']),
                          (None, [Msg('null')],
                           ['checkpoint', 'sleep', 'rewindable',
                            'wait_for', 'null', 'rewindable', 'sleep']),
                          ([Msg('null')], [Msg('null')],
                           ['checkpoint', 'sleep', 'rewindable', 'null',
                            'wait_for', 'null', 'rewindable', 'sleep'])])
def test_pre_suspend_plan(fresh_RE, pre_plan, post_plan, expected_list):
    RE = fresh_RE
    sig = ophyd.Signal()
    scan = [Msg('checkpoint'), Msg('sleep', None, .2)]
    msg_lst = []
    sig.put(0)

    def accum(msg):
        msg_lst.append(msg)

    susp = SuspendBoolHigh(sig, pre_plan=pre_plan,
                           post_plan=post_plan)

    RE.install_suspender(susp)
    RE._loop.call_later(.1, sig.put, 1)
    RE._loop.call_later(1, sig.put, 0)
    RE.msg_hook = accum
    RE(scan)

    assert len(msg_lst) == len(expected_list)
    assert expected_list == [m[0] for m in msg_lst]

    RE.remove_suspender(susp)
    RE(scan)
    assert susp.RE is None

    RE.install_suspender(susp)
    RE.clear_suspenders()
    assert susp.RE is None
    assert not RE.suspenders


def test_pause_from_suspend(fresh_RE):
    'Tests what happens when a pause is requested from a suspended state'
    RE = fresh_RE
    sig = ophyd.Signal()
    scan = [Msg('checkpoint')]
    msg_lst = []
    sig.put(1)

    def accum(msg):
        msg_lst.append(msg)

    susp = SuspendBoolHigh(sig)

    RE.install_suspender(susp)
    RE._loop.call_later(1, RE.request_pause)
    RE._loop.call_later(2, sig.put, 0)
    RE.msg_hook = accum
    RE(scan)
    assert [m[0] for m in msg_lst] == ['wait_for']
    RE.resume()
    assert ['wait_for', 'wait_for', 'checkpoint'] == [m[0] for m in msg_lst]


def test_deferred_pause_from_suspend(fresh_RE):
    'Tests what happens when a soft pause is requested from a suspended state'
    RE = fresh_RE
    sig = ophyd.Signal()
    scan = [Msg('checkpoint'), Msg('null')]
    msg_lst = []
    sig.put(1)

    def accum(msg):
        print(msg)
        msg_lst.append(msg)

    susp = SuspendBoolHigh(sig)

    RE.install_suspender(susp)
    RE._loop.call_later(1, RE.request_pause, True)
    RE._loop.call_later(4, sig.put, 0)
    RE.msg_hook = accum
    RE(scan)
    assert [m[0] for m in msg_lst] == ['wait_for', 'checkpoint']
    RE.resume()
    assert ['wait_for', 'checkpoint', 'null'] == [m[0] for m in msg_lst]


def test_unresumable_suspend_fail(fresh_RE):
    'Tests what happens when a soft pause is requested from a suspended state'
    RE = fresh_RE

    scan = [Msg('clear_checkpoint'), Msg('sleep', None, 50)]
    m_coll = MsgCollector()
    RE.msg_hook = m_coll

    ev = asyncio.Event(loop=RE.loop)
    loop = RE.loop
    loop.call_later(.1, partial(RE.request_suspend, fut=ev.wait()))
    loop.call_later(1, ev.set)
    start = time.time()
    with pytest.raises(RunEngineInterrupted):
        RE(scan, raise_if_interrupted=True)
    stop = time.time()
    assert .1 < stop - start < 1
