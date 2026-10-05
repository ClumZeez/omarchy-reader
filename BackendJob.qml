import QtQuick
import Quickshell.Io

// One backend invocation at a time. Reports once both the exit status and
// the collected output have arrived (their order is not defined), or when
// the helper never starts or outlives its deadline (`timedOut` tells the
// two apart for whoever words the message).
//
// A running Process ignores a new command, so a request made while busy is
// held and replaces any older held one; the caller's handler should ignore
// a result when `queued` is set, because a newer request supersedes it.
Process {
  id: job

  property int timeoutMs: 30000
  property var queued: null
  property bool busy: false
  property string output: ""
  property bool timedOut: false

  property bool _started: false
  property bool _gotExit: false
  property bool _gotOutput: false
  property bool _failed: false
  property int _code: 0
  property int _status: 0

  // ok: exited normally with status 0. `output` is stdout either way: the
  // backend prints its error object there on failure.
  signal finished(bool ok, string output)

  function run(argv) {
    if (busy) {
      queued = argv
      return
    }
    queued = null
    busy = true
    output = ""
    _started = false
    _gotExit = false
    _gotOutput = false
    _failed = false
    timedOut = false
    command = argv
    running = true
    _launch.restart()
    _deadline.restart()
  }

  function _settle() {
    if (!busy || !_gotExit || !_gotOutput) return
    _launch.stop()
    _deadline.stop()
    _grace.stop()
    _reap.stop()
    busy = false
    var ok = !_failed && _code === 0 && _status === 0
    finished(ok, output)
    if (queued) run(queued)
  }

  // Give up on the helper. A started process is stopped and its exit awaited
  // before settling, because the next request cannot start while it lives.
  function _abandon() {
    _failed = true
    _gotOutput = true
    if (_started && running) {
      running = false
      _reap.restart()
      return
    }
    _gotExit = true
    _settle()
  }

  stdout: StdioCollector {
    id: collector
    waitForEnd: true
    onStreamFinished: {
      if (!job.busy || job._gotOutput) return
      job.output = String(text || "")
      job._gotOutput = true
      job._settle()
    }
  }

  onStarted: _started = true

  onExited: function(exitCode, exitStatus) {
    if (!busy || _gotExit) return
    _code = exitCode
    _status = exitStatus
    _gotExit = true
    if (!_gotOutput) _grace.restart()
    _settle()
  }

  // Output normally lands with the exit; if the stream never reports, take
  // what was collected rather than wait for the deadline.
  property Timer _grace: Timer {
    interval: 300
    onTriggered: {
      if (!job.busy || job._gotOutput) return
      job.output = String(collector.text || "")
      job._gotOutput = true
      job._settle()
    }
  }

  // No python3, or a missing helper: nothing ever starts.
  property Timer _launch: Timer {
    interval: 4000
    onTriggered: if (job.busy && !job._started) job._abandon()
  }

  property Timer _deadline: Timer {
    interval: job.timeoutMs
    onTriggered: {
      if (!job.busy) return
      job.timedOut = true
      job._abandon()
    }
  }

  property Timer _reap: Timer {
    interval: 2000
    onTriggered: {
      if (!job.busy) return
      job._gotExit = true
      job._settle()
    }
  }
}
