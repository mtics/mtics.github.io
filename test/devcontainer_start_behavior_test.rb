# frozen_string_literal: true

require "minitest/autorun"
require "open3"
require "socket"
require "tmpdir"

class DevcontainerStartBehaviorTest < Minitest::Test
  ROOT = File.expand_path("..", __dir__)
  LAUNCHER = File.join(ROOT, "bin/devcontainer_start.sh")

  def test_surfaces_an_early_server_failure
    Dir.mktmpdir("devcontainer-start-failure") do |directory|
      entrypoint = File.join(directory, "failing-entrypoint")
      pid_file = File.join(directory, "jekyll.pid")
      log_file = File.join(directory, "jekyll.log")
      File.write(entrypoint, "#!/bin/sh\necho 'fixture start failed'\nexit 23\n")
      File.chmod(0o755, entrypoint)
      probe = TCPServer.new("127.0.0.1", 0)
      unused_port = probe.addr[1]
      probe.close

      stdout, stderr, status = Open3.capture3(
        {
          "DEVCONTAINER_ENTRYPOINT" => entrypoint,
          "DEVCONTAINER_PID_FILE" => pid_file,
          "DEVCONTAINER_LOG_FILE" => log_file,
          "DEVCONTAINER_PROCESS_PATTERN" => "no-such-jekyll-failure-#{Process.pid}",
          "DEVCONTAINER_HEALTH_PORT" => unused_port.to_s,
          "DEVCONTAINER_STARTUP_ATTEMPTS" => "10",
          "DEVCONTAINER_STARTUP_INTERVAL_SECONDS" => "0.1"
        },
        LAUNCHER,
        chdir: ROOT
      )

      refute status.success?, "postStart must fail when the server exits during startup:\n#{stdout}\n#{stderr}"
      assert_includes stderr, "fixture start failed"
      refute File.exist?(pid_file), "a failed server must not leave a live-looking PID file"
    end
  end

  def test_reuses_the_running_server
    Dir.mktmpdir("devcontainer-start-idempotent") do |directory|
      entrypoint = File.join(directory, "long-running-entrypoint")
      pid_file = File.join(directory, "jekyll.pid")
      log_file = File.join(directory, "jekyll.log")
      starts_file = File.join(directory, "starts")
      process_token = "mtics-jekyll-idempotent-#{Process.pid}"
      probe = TCPServer.new("127.0.0.1", 0)
      health_port = probe.addr[1]
      probe.close
      File.write(entrypoint, <<~SH)
        #!/bin/sh
        printf 'started\n' >> "$STARTS_FILE"
        exec ruby -rsocket -e 'server = TCPServer.new("127.0.0.1", Integer(ENV.fetch("DEVCONTAINER_HEALTH_PORT"))); trap("TERM") { exit }; loop { server.accept.close }' "$PROCESS_TOKEN"
      SH
      File.chmod(0o755, entrypoint)
      env = {
        "DEVCONTAINER_ENTRYPOINT" => entrypoint,
        "DEVCONTAINER_PID_FILE" => pid_file,
        "DEVCONTAINER_LOG_FILE" => log_file,
        "DEVCONTAINER_PROCESS_PATTERN" => process_token,
        "DEVCONTAINER_HEALTH_PORT" => health_port.to_s,
        "DEVCONTAINER_STARTUP_ATTEMPTS" => "20",
        "DEVCONTAINER_STARTUP_INTERVAL_SECONDS" => "0.1",
        "DEVCONTAINER_STARTUP_GRACE_SECONDS" => "0.05",
        "PROCESS_TOKEN" => process_token,
        "STARTS_FILE" => starts_file
      }
      server_pid = nil

      begin
        _first_stdout, first_stderr, first_status = Open3.capture3(env, LAUNCHER, chdir: ROOT)
        assert first_status.success?, first_stderr
        server_pid = Integer(File.read(pid_file).strip)

        second_stdout, second_stderr, second_status = Open3.capture3(env, LAUNCHER, chdir: ROOT)
        assert second_status.success?, second_stderr
        assert_equal server_pid, Integer(File.read(pid_file).strip)
        assert_equal ["started\n"], File.readlines(starts_file)
        assert_includes second_stdout, "already running"
      ensure
        begin
          Process.kill("TERM", server_pid) if server_pid
        rescue Errno::ESRCH
          nil
        end
      end
    end
  end

  def test_rejects_a_recycled_unrelated_pid
    Dir.mktmpdir("devcontainer-start-stale-pid") do |directory|
      entrypoint = File.join(directory, "server-entrypoint")
      pid_file = File.join(directory, "jekyll.pid")
      log_file = File.join(directory, "jekyll.log")
      process_token = "mtics-jekyll-stale-pid-#{Process.pid}"
      probe = TCPServer.new("127.0.0.1", 0)
      health_port = probe.addr[1]
      probe.close
      File.write(entrypoint, <<~SH)
        #!/bin/sh
        exec ruby -rsocket -e 'server = TCPServer.new("127.0.0.1", Integer(ENV.fetch("DEVCONTAINER_HEALTH_PORT"))); trap("TERM") { exit }; loop { server.accept.close }' "$PROCESS_TOKEN"
      SH
      File.chmod(0o755, entrypoint)
      unrelated_pid = Process.spawn("sleep", "30", out: File::NULL, err: File::NULL)
      File.write(pid_file, "#{unrelated_pid}\n")
      server_pid = nil

      begin
        _stdout, stderr, status = Open3.capture3(
          {
            "DEVCONTAINER_ENTRYPOINT" => entrypoint,
            "DEVCONTAINER_PID_FILE" => pid_file,
            "DEVCONTAINER_LOG_FILE" => log_file,
            "DEVCONTAINER_PROCESS_PATTERN" => process_token,
            "DEVCONTAINER_HEALTH_PORT" => health_port.to_s,
            "DEVCONTAINER_STARTUP_ATTEMPTS" => "20",
            "DEVCONTAINER_STARTUP_INTERVAL_SECONDS" => "0.05",
            "DEVCONTAINER_STARTUP_GRACE_SECONDS" => "0.05",
            "PROCESS_TOKEN" => process_token
          },
          LAUNCHER,
          chdir: ROOT
        )
        assert status.success?, stderr
        server_pid = Integer(File.read(pid_file).strip)
        refute_equal unrelated_pid, server_pid, "a recycled PID must not be trusted as Jekyll"
        assert Process.kill(0, unrelated_pid), "the unrelated process must not be killed"
      ensure
        [server_pid, unrelated_pid].compact.each do |pid|
          begin
            Process.kill("TERM", pid)
          rescue Errno::ESRCH
            nil
          end
          Process.wait(pid) if pid == unrelated_pid
        rescue Errno::ECHILD
          nil
        end
      end
    end
  end

  def test_rejects_readiness_from_an_unrelated_listener
    Dir.mktmpdir("devcontainer-start-false-readiness") do |directory|
      entrypoint = File.join(directory, "non-listening-entrypoint")
      pid_file = File.join(directory, "jekyll.pid")
      log_file = File.join(directory, "jekyll.log")
      listener = TCPServer.new("127.0.0.1", 0)
      health_port = listener.addr[1]
      File.write(entrypoint, <<~SH)
        #!/bin/sh
        echo 'fixture stays alive without owning the listener'
        trap 'exit 0' TERM INT
        while :; do sleep 1; done
      SH
      File.chmod(0o755, entrypoint)
      server_pid = nil

      begin
        _stdout, stderr, status = Open3.capture3(
          {
            "DEVCONTAINER_ENTRYPOINT" => entrypoint,
            "DEVCONTAINER_PID_FILE" => pid_file,
            "DEVCONTAINER_LOG_FILE" => log_file,
            "DEVCONTAINER_PROCESS_PATTERN" => "no-such-jekyll-listener-#{Process.pid}",
            "DEVCONTAINER_HEALTH_PORT" => health_port.to_s,
            "DEVCONTAINER_STARTUP_ATTEMPTS" => "3",
            "DEVCONTAINER_STARTUP_INTERVAL_SECONDS" => "0.05",
            "DEVCONTAINER_STARTUP_GRACE_SECONDS" => "0.05"
          },
          LAUNCHER,
          chdir: ROOT
        )
        server_pid = Integer(File.read(pid_file).strip) if File.file?(pid_file)

        refute status.success?, "an unrelated open port must not make a non-listening child healthy"
        assert_includes stderr, "timed out"
        refute File.exist?(pid_file), "the failed child must not leave a PID file"
      ensure
        listener.close
        begin
          Process.kill("KILL", server_pid) if server_pid
        rescue Errno::ESRCH
          nil
        end
      end
    end
  end

  def test_timeout_does_not_kill_an_adopted_matching_process
    Dir.mktmpdir("devcontainer-start-adopted") do |directory|
      pid_file = File.join(directory, "jekyll.pid")
      log_file = File.join(directory, "jekyll.log")
      process_token = "mtics-jekyll-adopted-#{Process.pid}"
      probe = TCPServer.new("127.0.0.1", 0)
      unused_port = probe.addr[1]
      probe.close
      adopted_pid = Process.spawn(
        "ruby", "-e", "trap('TERM') { exit 42 }; loop { sleep 1 }", process_token,
        out: File::NULL, err: File::NULL
      )
      File.write(pid_file, "#{adopted_pid}\n")
      reaped = false

      begin
        _stdout, stderr, status = Open3.capture3(
          {
            "DEVCONTAINER_ENTRYPOINT" => "/no/such/entrypoint",
            "DEVCONTAINER_PID_FILE" => pid_file,
            "DEVCONTAINER_LOG_FILE" => log_file,
            "DEVCONTAINER_PROCESS_PATTERN" => process_token,
            "DEVCONTAINER_HEALTH_PORT" => unused_port.to_s,
            "DEVCONTAINER_STARTUP_ATTEMPTS" => "2",
            "DEVCONTAINER_STARTUP_INTERVAL_SECONDS" => "0.05",
            "DEVCONTAINER_STARTUP_GRACE_SECONDS" => "0.05"
          },
          LAUNCHER,
          chdir: ROOT
        )

        refute status.success?
        assert_includes stderr, "timed out"
        exited_pid = Process.waitpid(adopted_pid, Process::WNOHANG)
        reaped = !exited_pid.nil?
        assert_nil exited_pid, "a launcher must never signal a matching process it did not start"
        refute File.exist?(pid_file), "the stale launcher PID file should still be removed"
      ensure
        unless reaped
          begin
            Process.kill("KILL", adopted_pid)
          rescue Errno::ESRCH
            nil
          end
          begin
            Process.wait(adopted_pid)
          rescue Errno::ECHILD
            nil
          end
        end
      end
    end
  end

  def test_fails_when_the_server_never_becomes_ready
    Dir.mktmpdir("devcontainer-start-timeout") do |directory|
      entrypoint = File.join(directory, "never-ready-entrypoint")
      pid_file = File.join(directory, "jekyll.pid")
      log_file = File.join(directory, "jekyll.log")
      File.write(entrypoint, <<~SH)
        #!/bin/sh
        echo 'fixture never became ready'
        trap 'exit 0' TERM INT
        while :; do sleep 1; done
      SH
      File.chmod(0o755, entrypoint)
      probe = TCPServer.new("127.0.0.1", 0)
      unused_port = probe.addr[1]
      probe.close
      server_pid = nil

      begin
        _stdout, stderr, status = Open3.capture3(
          {
            "DEVCONTAINER_ENTRYPOINT" => entrypoint,
            "DEVCONTAINER_PID_FILE" => pid_file,
            "DEVCONTAINER_LOG_FILE" => log_file,
            "DEVCONTAINER_PROCESS_PATTERN" => "no-such-jekyll-timeout-#{Process.pid}",
            "DEVCONTAINER_HEALTH_PORT" => unused_port.to_s,
            "DEVCONTAINER_STARTUP_ATTEMPTS" => "3",
            "DEVCONTAINER_STARTUP_INTERVAL_SECONDS" => "0.1",
            "DEVCONTAINER_STARTUP_GRACE_SECONDS" => "0.1"
          },
          LAUNCHER,
          chdir: ROOT
        )
        server_pid = Integer(File.read(pid_file).strip) if File.file?(pid_file)

        refute status.success?, "postStart must fail if TCP readiness never succeeds"
        assert_includes stderr, "timed out"
        refute File.exist?(pid_file), "a timed-out server must not leave a PID file"
      ensure
        begin
          Process.kill("KILL", server_pid) if server_pid
        rescue Errno::ESRCH
          nil
        end
      end
    end
  end
end
