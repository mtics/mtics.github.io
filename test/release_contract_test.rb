# frozen_string_literal: true

require "minitest/autorun"
require "date"
require "digest"
require "json"
require "open3"
require "tmpdir"
require "yaml"

class ReleaseContractTest < Minitest::Test
  ROOT = File.expand_path("..", __dir__)
  SHELL_ENTRY_POINTS = %w[
    bin/entry_point.sh
    bin/devcontainer_start.sh
    bin/dependency_audit
    bin/cibuild
    bin/deploy
    test/devcontainer_post_create_smoke.sh
  ].freeze
  WORKFLOW_PATHS = %w[
    .github/workflows/deploy.yml
    .github/workflows/update_scholar_citations.yml
  ].freeze
  # These immutable releases were checked against their official action.yml
  # manifests. Direct actions declare node24. upload-pages-artifact is a
  # composite that delegates to upload-artifact v7.0.0 (node24) at the pinned
  # bbbca2ddaa5d8feaa63e36b76fdaad77386f024f commit.
  ACTION_PINS = {
    "actions/checkout" => ["9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0", "v7.0.0"],
    "actions/configure-pages" => ["45bfe0192ca1faeb007ade9deae92b16b8254a0d", "v6.0.0"],
    "actions/deploy-pages" => ["cd2ce8fcbc39b97be8ca5fce6e763baed58fa128", "v5.0.0"],
    "actions/download-artifact" => ["3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c", "v8.0.1"],
    "actions/setup-python" => ["ece7cb06caefa5fff74198d8649806c4678c61a1", "v6.3.0"],
    "actions/upload-artifact" => ["043fb46d1a93c77aae656e7c1c64a875d1fc6a0a", "v7.0.1"],
    "actions/upload-pages-artifact" => ["fc324d3547104276b827a68afc52ff2a11cc49c9", "v5.0.0"]
  }.freeze
  RELEASE_INPUTS = %w[
    .al-folio-overrides.yml
    .devcontainer/Dockerfile
    .devcontainer/devcontainer.json
    .dockerignore
    Dockerfile
    Gemfile
    Gemfile.lock
    _config.yml
    docker-compose.yml
    docker-compose-slim.yml
    package.json
    package-lock.json
    requirements.txt
    requirements-build.in
    requirements-build.txt
    requirements-citations.in
    requirements-citations.txt
    .github/dependabot.yml
    .security-scanner-gaps.yml
    bin/dependency_audit
    bin/create_trivy_baseline.py
    bin/create_trivy_db_provenance.py
    bin/enforce_trivy_report.py
    bin/validate_trivy_oci_manifest.py
    bin/update_scholar_citations.py
    .trivy-unfixed-baseline.json
    .trivy-baseline-review.json
    _data/cv.yml
    _plugins/local_asset_cache_bust.rb
    assets/pdf/cv.pdf
  ].freeze
  PRODUCTION_SOURCE_GLOBS = %w[
    _includes/**/*.liquid
    _layouts/**/*.liquid
    _plugins/**/*.rb
    _sass/**/*.scss
    assets/js/**/*.js
  ].freeze

  def read(path)
    File.read(File.join(ROOT, path))
  end

  def load_workflow(path)
    document = YAML.load_file(File.join(ROOT, path))
    document["on"] ||= document.delete(true)
    document
  end

  def capture(*command)
    Open3.capture3(*command, chdir: ROOT)
  end

  def capture_deploy(*arguments)
    Dir.mktmpdir("deploy-contract") do |directory|
      fake_bin = File.join(directory, "bin")
      bundle_log = File.join(directory, "bundle.log")
      Dir.mkdir(fake_bin)
      fake_bundle = File.join(fake_bin, "bundle")
      File.write(fake_bundle, <<~SH)
        #!/bin/sh
        printf '%s\\n' "$@" > "$BUNDLE_LOG"
      SH
      File.chmod(0o755, fake_bundle)

      stdout, stderr, status = Open3.capture3(
        {
          "BUNDLE_LOG" => bundle_log,
          "PATH" => "#{fake_bin}:#{ENV.fetch('PATH')}"
        },
        File.join(ROOT, "bin/deploy"),
        *arguments,
        chdir: directory
      )
      invocation = File.file?(bundle_log) ? File.read(bundle_log).lines.map(&:chomp) : []
      [stdout, stderr, status, invocation]
    end
  end

  def requirement_blocks(path)
    lines = read(path).lines
    starts = lines.each_index.select { |index| lines[index].match?(/\A[A-Za-z0-9][A-Za-z0-9_.-]*==/) }
    starts.map.with_index do |start, index|
      finish = starts.fetch(index + 1, lines.length)
      lines[start...finish].join
    end
  end

  def test_lockfile_is_present_and_not_ignored
    lockfile = File.join(ROOT, "Gemfile.lock")
    assert File.file?(lockfile), "Gemfile.lock must be generated from Gemfile"

    _stdout, _stderr, status = capture("git", "check-ignore", "--no-index", "-q", "Gemfile.lock")
    refute status.success?, "Gemfile.lock must not be ignored"

    lock = File.read(lockfile)
    assert_match(/^CHECKSUMS\n(?:  .+\n)+/m, lock, "Gemfile.lock must contain dependency checksums")
    assert_match(/^BUNDLED WITH\n\s+2\.6\.9\s*$/m, lock, "Gemfile.lock must pin Bundler 2.6.9")
    platforms = lock.lines
                    .drop_while { |line| line != "PLATFORMS\n" }
                    .drop(1)
                    .take_while { |line| line.start_with?("  ") }
                    .map(&:strip)
    assert_includes platforms, "x86_64-linux"
  end

  def test_python_bytecode_and_cache_directories_are_ignored
    gitignore = read(".gitignore")
    assert_match(/^__pycache__\/$/, gitignore)
    assert_match(/^\*\.py\[cod\]$/, gitignore)

    _stdout, stderr, status = capture(
      "git", "check-ignore", "--no-index", "-q", "bin/__pycache__/release-contract.pyc"
    )
    assert status.success?, "Python bytecode must remain outside release snapshots: #{stderr}"
  end

  def test_generated_reproducible_config_is_ignored
    gitignore = read(".gitignore")
    assert_match(/^\.jekyll-reproducible\.yml$/, gitignore)

    _stdout, stderr, status = capture(
      "git", "check-ignore", "--no-index", "-q", ".jekyll-reproducible.yml"
    )
    assert status.success?, "the workflow-generated build config must not enter release snapshots: #{stderr}"
  end

  def test_container_entry_points_are_directly_executable_shell_scripts
    SHELL_ENTRY_POINTS.each do |path|
      absolute_path = File.join(ROOT, path)
      assert File.executable?(absolute_path), "#{path} must have an executable bit"
      assert_match(/\A#!.*(?:ba)?sh\b/, File.read(absolute_path), "#{path} needs a shell shebang")

      _stdout, stderr, status = capture("bash", "-n", path)
      assert status.success?, "#{path} must pass bash -n: #{stderr}"
    end
  end

  def test_delivery_entrypoint_execs_one_foreground_jekyll_server
    entrypoint = read("bin/entry_point.sh")

    assert_match(/^exec bundle _2\.6\.9_ exec jekyll serve\b/, entrypoint)
    refute_match(/^\s*bundle (?!_2\.6\.9_\b)/, entrypoint)
    assert_includes entrypoint, "bundle _2.6.9_ check"
    refute_includes entrypoint, "bundle _2.6.9_ install",
                    "the immutable delivery image must never mutate dependencies at runtime"
    assert_match(/rebuild.*image/i, entrypoint)
    assert_includes entrypoint, "--disable-disk-cache"
    refute_match(/^\s*while\s+true\b/, entrypoint)
    refute_includes entrypoint, "inotifywait"
    refute_match(/jekyll serve[^\n]*&\s*$/, entrypoint)
  end

  def test_devcontainer_uses_an_idempotent_post_start_launcher
    devcontainer = read(".devcontainer/devcontainer.json")

    assert_match(/"postStartCommand"\s*:\s*"\.\/bin\/devcontainer_start\.sh"/, devcontainer)
    refute_match(/"postAttachCommand"\s*:/, devcontainer)
    assert File.file?(File.join(ROOT, "bin/devcontainer_start.sh"))
  end

  def test_candidate_snapshot_tracks_release_inputs_and_executable_modes
    return unless ENV["VERIFY_GIT_INDEX"] == "1"

    _stdout, stderr, status = capture("git", "diff", "--quiet", "--")
    assert status.success?, "candidate contains unstaged tracked changes; stage every intended file before release: #{stderr}"

    untracked, stderr, status = capture("git", "ls-files", "--others", "--exclude-standard")
    assert status.success?, "cannot inspect untracked candidate files: #{stderr}"
    assert_empty untracked.lines.map(&:strip).reject(&:empty?),
                 "candidate contains non-ignored files absent from the Git index"

    test_sources = Dir.glob(File.join(ROOT, "test/**/*.{json,mjs,py,rb,txt,yml}"))
                      .map { |path| path.delete_prefix("#{ROOT}/") }
    production_sources = PRODUCTION_SOURCE_GLOBS.flat_map do |pattern|
      Dir.glob(File.join(ROOT, pattern))
    end.select { |path| File.file?(path) }
      .map { |path| path.delete_prefix("#{ROOT}/") }
    override_paths = YAML.load_file(File.join(ROOT, ".al-folio-overrides.yml"))
                         .fetch("overrides")
                         .keys
    snapshot_paths = RELEASE_INPUTS + WORKFLOW_PATHS + SHELL_ENTRY_POINTS + test_sources + production_sources + override_paths
    snapshot_paths.uniq.sort.each do |path|
      _stdout, stderr, status = capture("git", "ls-files", "--error-unmatch", "--", path)
      assert status.success?, "release input is absent from the Git snapshot: #{path}: #{stderr}"
    end

    SHELL_ENTRY_POINTS.each do |path|
      stdout, stderr, status = capture("git", "ls-files", "--stage", "--error-unmatch", "--", path)
      assert status.success?, "cannot inspect Git mode for #{path}: #{stderr}"
      assert_match(/\A100755\s/, stdout, "#{path} must be committed with mode 100755")
    end
  end

  def test_candidate_snapshot_inventory_covers_site_owned_runtime_sources
    production_sources = PRODUCTION_SOURCE_GLOBS.flat_map do |pattern|
      Dir.glob(File.join(ROOT, pattern))
    end.select { |path| File.file?(path) }
      .map { |path| path.delete_prefix("#{ROOT}/") }

    %w[
      _includes/cv/list.liquid
      _includes/cv/normal.liquid
      _includes/cv/one_line.liquid
      _includes/cv/social_networks.liquid
      _includes/cv/text.liquid
      assets/js/back-to-top.js
      assets/js/bibliography.js
      assets/js/search-result-filter.js
    ].each do |path|
      assert_includes production_sources, path
    end
  end

  def test_workflow_actions_are_immutable_and_version_documented
    observed_actions = []

    WORKFLOW_PATHS.each do |path|
      action_lines = read(path).lines.each_with_index.map do |line, index|
        match = line.match(/^\s*uses:\s*([^\s#]+)(?:\s+#\s*(\S.*))?$/)
        [index + 1, match[1], match[2]] if match
      end.compact
      refute_empty action_lines, "#{path} must contain Actions"

      action_lines.each do |line_number, action, comment|
        assert_match(/@[0-9a-f]{40}\z/, action,
                     "#{path}:#{line_number} must pin the Action to a full commit SHA")
        assert_match(/\Av\d+(?:\.\d+){1,2}\z/, comment.to_s.strip,
                     "#{path}:#{line_number} must document the pinned semantic version")

        action_name, commit = action.split("@", 2)
        expected_commit, expected_version = ACTION_PINS.fetch(action_name) do
          flunk "#{path}:#{line_number} uses unreviewed Action #{action_name}"
        end
        assert_equal expected_commit, commit,
                     "#{path}:#{line_number} must use the reviewed native-Node24 Action release"
        assert_equal expected_version, comment.to_s.strip,
                     "#{path}:#{line_number} must document the reviewed release"
        observed_actions << action_name
      end
    end

    assert_equal ACTION_PINS.keys.sort, observed_actions.uniq.sort,
                 "the reviewed Action inventory must match the workflows"
  end

  def test_dependabot_weekly_tracks_every_release_dependency_ecosystem
    path = File.join(ROOT, ".github/dependabot.yml")
    assert File.file?(path), "Dependabot configuration must prevent release runtimes from going stale"

    updates = YAML.load_file(path).fetch("updates")
    ecosystems = updates.map { |entry| entry.fetch("package-ecosystem") }.uniq.sort
    assert_equal %w[bundler docker github-actions npm pip], ecosystems
    updates.each do |entry|
      assert_equal "weekly", entry.dig("schedule", "interval")
      assert_operator entry.fetch("open-pull-requests-limit"), :<=, 3
      refute_empty entry.fetch("groups"), "#{entry.fetch('package-ecosystem')} updates should be grouped"
    end

    docker_directories = updates
                         .select { |entry| entry.fetch("package-ecosystem") == "docker" }
                         .map { |entry| entry.fetch("directory") }
                         .sort
    assert_equal ["/", "/.devcontainer"], docker_directories
  end

  def test_docker_uses_a_fixed_supported_bookworm_toolchain
    dockerfile = read("Dockerfile")
    final_stage = dockerfile.split(/^FROM /).drop(1).last
    ruby_images = dockerfile.lines.select { |line| line.start_with?("FROM ruby:") }
    ruby_image = ruby_images.find { |line| !line.match?(/\s+AS\s+/i) }
    ruby_builder_image = ruby_images.find { |line| line.match?(/\s+AS\s+bundle-builder\s*$/i) }
    node_image = dockerfile.lines.find { |line| line.start_with?("FROM node:") }
    python_image = dockerfile.lines.find { |line| line.start_with?("FROM python:") }
    assert_equal 2, ruby_images.length,
                 "delivery must contain one pinned bundle builder and one pinned runtime"
    assert_match(/\AFROM ruby:3\.4\.10-slim-bookworm@sha256:[0-9a-f]{64}\s*\z/, ruby_image)
    assert_match(/\AFROM ruby:3\.4\.10-slim-bookworm@sha256:[0-9a-f]{64}\s+AS\s+bundle-builder\s*\z/i,
                 ruby_builder_image)
    assert_match(/\AFROM node:24\.18\.0-bookworm-slim@sha256:[0-9a-f]{64}\s+AS\s+node-runtime\s*\z/i,
                 node_image)
    assert_match(/\AFROM python:3\.13\.14-slim-bookworm@sha256:[0-9a-f]{64}\s+AS\s+python-runtime\s*\z/i,
                 python_image)

    expected_versions = {
      "BUNDLER_VERSION" => "2.6.9",
      "NPM_VERSION" => "11.18.0",
      "PYTHON_VERSION" => "3.13.14"
    }
    expected_versions.each do |name, version|
      assert_match(/^ARG #{name}=#{Regexp.escape(version)}$/, dockerfile)
    end
    assert_includes dockerfile, 'bundle _${BUNDLER_VERSION}_ --version'
    refute_match(/gem install[^\n]*bundler/i, dockerfile,
                 "the digest-pinned Ruby base already supplies Bundler; do not bootstrap it from the network")
    assert_match(/python3 --version.*PYTHON_VERSION/, dockerfile)
    assert_match(%r{COPY --from=python-runtime /usr/local /usr/local}, dockerfile)
    apt_packages = final_stage
                   .match(/apt-get install -y --no-install-recommends \\\n(?<packages>.*?) && \\\n\s*bundle/m)
                   &.named_captures
                   &.fetch("packages", nil)
                   &.lines
                   &.map { |line| line.delete("\\").strip }
                   &.reject(&:empty?)
    refute_nil apt_packages, "the delivery APT package boundary must remain machine-checkable"
    refute_includes apt_packages, "python3",
                    "Python is supplied by the digest-pinned python-runtime image"
    refute_includes apt_packages, "python3-pip",
                    "pip is supplied by the digest-pinned python-runtime image"
    copy_index = dockerfile.index(/COPY\s+Gemfile\s+Gemfile\.lock/)
    install_index = dockerfile.index(/bundle install/)
    refute_nil copy_index, "Gemfile and Gemfile.lock must be copied together"
    refute_nil install_index, "Docker image must install the locked bundle"
    assert_operator copy_index, :<, install_index

    requirements_copy_index = dockerfile.index(/COPY\s+requirements-build\.txt/)
    python_install_index = dockerfile.index(/pip install.*--require-hashes.*requirements-build\.txt/)
    refute_nil requirements_copy_index, "Docker must copy the hashed build requirements"
    refute_nil python_install_index, "Docker must install Python packages with hash checking"
    assert_operator requirements_copy_index, :<, python_install_index

    assert_match(/^nbconvert==7\.17\.1\b/, read("requirements-build.txt"))
  end

  def test_delivery_compilers_are_confined_to_a_pinned_bundle_builder
    dockerfile = read("Dockerfile")
    stages = dockerfile.split(/^FROM /).drop(1)
    bundle_builder = stages.find { |stage| stage.lines.first.include?(" AS bundle-builder") }
    refute_nil bundle_builder, "native gems must be compiled outside the production image"

    final_stage = stages.last
    assert_match(/\Aruby:3\.4\.10-slim-bookworm@sha256:[0-9a-f]{64}\s*\n/, final_stage)
    assert_includes bundle_builder, "bundle install --jobs 4 --retry 3"
    assert_includes final_stage,
                    "COPY --from=bundle-builder /usr/local/bundle /usr/local/bundle"
    assert_includes final_stage, "bundle _${BUNDLER_VERSION}_ check"
    refute_match(/^\s+build-essential \\$/m, final_stage,
                 "the production image must not ship a compiler toolchain")
    refute_match(/^\s+zlib1g-dev(?: \\)?$/m, final_stage,
                 "the production image must not ship development headers")
    {
      "inotify-tools" => "force-polling Jekyll has no inotify-tools CLI caller",
      "lsof" => "lsof is required only by the development launcher",
      "procps" => "procps must not be a direct delivery dependency; Chromium may pull it transitively"
    }.each do |package, rationale|
      refute_match(/^\s+#{Regexp.escape(package)}(?: \\)?$/m, final_stage, rationale)
    end
    refute_includes final_stage, "bundle install --jobs 4 --retry 3"
  end


  def test_python_automation_dependencies_are_fully_hashed
    expected_direct_dependencies = {
      "requirements-build.txt" => %w[nbconvert==7.17.1 pip-audit==2.10.1 playwright==1.61.0 rendercv==2.8],
      "requirements-citations.txt" => %w[serpapi==1.0.2 pyyaml==6.0.3]
    }

    expected_direct_dependencies.each do |path, dependencies|
      flunk "#{path} must exist" unless File.file?(File.join(ROOT, path))

      blocks = requirement_blocks(path)
      refute_empty blocks, "#{path} must contain a compiled dependency graph"
      blocks.each do |block|
        assert_includes block, "--hash=sha256:", "unhashed requirement in #{path}: #{block.lines.first.strip}"
      end
      dependencies.each { |dependency| assert_match(/^#{Regexp.escape(dependency)}\b/i, read(path)) }
    end

    root_requirements = read("requirements.txt").lines
                        .map(&:strip)
                        .reject { |line| line.empty? || line.start_with?("#") }
    assert_equal ["-r requirements-build.txt", "-r requirements-citations.txt"], root_requirements
  end

  def test_compiled_requirement_inputs_are_minimal_and_present
    expected_inputs = {
      "requirements-build.in" => [
        "nbconvert==7.17.1", "pip-audit==2.10.1", "playwright==1.61.0", "rendercv[full]==2.8.0"
      ],
      "requirements-citations.in" => ["serpapi==1.0.2", "PyYAML==6.0.3"]
    }

    expected_inputs.each do |path, expected|
      flunk "#{path} must exist so its lock can be regenerated" unless File.file?(File.join(ROOT, path))

      actual = read(path).lines.map(&:strip).reject { |line| line.empty? || line.start_with?("#") }
      assert_equal expected, actual
    end
  end

  def test_legacy_scholar_client_is_absent
    refute_includes read("requirements-citations.in"), "google-search-results"
    refute_includes read("requirements-citations.txt"), "google-search-results"
    refute_includes read("bin/update_scholar_citations.py"), "GoogleSearch"
  end

  def test_python_locks_record_the_supported_interpreter_and_cutoff
    %w[build citations].each do |name|
      path = "requirements-#{name}.txt"
      expected_command = [
        "uv pip compile requirements-#{name}.in",
        "--python-version 3.13.14",
        "--universal",
        "--generate-hashes",
        "--exclude-newer 2026-07-12T00:00:00Z",
        "--output-file #{path}"
      ].join(" ")
      assert_includes read(path), expected_command,
                      "#{path} must document its reproducible lock command"
    end
  end

  def test_release_jobs_fail_closed_on_locked_dependency_vulnerabilities
    assert_includes read("Gemfile"), "gem 'bundler-audit', '= 0.9.3'"
    assert_match(/^\s{4}bundler-audit \(0\.9\.3\)$/, read("Gemfile.lock"))

    dockerfile = read("Dockerfile")
    refute_includes dockerfile,
                    "COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm",
                    "the vulnerable npm bundled in the Node base must not survive in an image layer"
    assert_includes dockerfile, "ln -sfn ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm"

    audit = read("bin/dependency_audit")
    assert_includes audit, "bundle exec bundler-audit check --update"
    assert_includes audit, "pip-audit --strict --requirement requirements-build.txt --no-deps --disable-pip"
    assert_includes audit, "pip-audit --strict --requirement requirements-citations.txt --no-deps --disable-pip"
    assert_includes audit, "npm audit --audit-level=moderate"

    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      image_index = steps.index { |step| step.fetch("run", "").include?("docker build --tag mtics-al-folio:ci") }
      audit_index = steps.index { |step| step.fetch("run", "").include?("./bin/dependency_audit") }
      refute_nil audit_index, "#{job_name} must audit all three locked ecosystems"
      assert_operator image_index, :<, audit_index
    end
  end

  def test_unused_distill_feature_is_not_installed_or_loaded
    config = YAML.load_file(File.join(ROOT, "_config.yml"))
    gemfile = read("Gemfile")
    lockfile = read("Gemfile.lock")

    assert_equal false, config.dig("al_folio", "features", "distill", "enabled")
    assert_equal false, config.dig("al_folio", "distill", "allow_remote_loader")
    refute_includes gemfile, "gem 'al_folio_distill'"
    refute_match(/^    al_folio_distill \(/, lockfile)
    refute_includes config.fetch("plugins"), "al_folio_distill"
  end

  def test_jsonresume_uses_only_jekyll_native_local_data
    config = YAML.load_file(File.join(ROOT, "_config.yml"))
    gemfile = read("Gemfile")
    lockfile = read("Gemfile.lock")

    refute config.key?("jekyll_get_json"),
           "remote or silently missing JSON must not enter the trusted CV rendering boundary"
    refute config.key?("jsonresume"),
           "an unconsumed section list must not imply that it controls JSON Resume rendering"
    refute_includes config.fetch("plugins"), "jekyll-get-json"
    refute_includes gemfile, "gem 'jekyll-get-json'"
    refute_match(/^    jekyll-get-json \(/, lockfile)
    assert_includes read("_pages/cv.md"), "_data/resume.json"
  end

  def test_liquid_filter_typos_and_missing_security_plugins_fail_closed
    liquid = YAML.load_file(File.join(ROOT, "_config.yml")).fetch("liquid")

    assert_equal "strict", liquid.fetch("error_mode")
    assert_equal true, liquid.fetch("strict_filters")
  end

  def test_publication_bibliography_is_intentionally_first_author_only
    source = read("_bibliography/papers.bib")
    entries = source.scan(/^@[A-Za-z]+\{([^,]+),\s*(.*?)(?=^@[A-Za-z]+\{|\z)/m)
    refute_empty entries

    violations = entries.each_with_object([]) do |(key, body), found|
      authors = body[/^\s*author\s*=\s*\{([^}]*)\}\s*,?\s*$/i, 1]
      first_author = authors.to_s.split(/\s+and\s+/i).first.to_s.split.join(" ")
      found << key unless first_author.match?(/\ALi,\s*Zhiwei\z/i)
    end
    assert_empty violations,
                 "papers.bib is scoped to Zhiwei Li's first-author publications: #{violations.join(', ')}"
  end

  def test_unused_global_script_features_are_disabled_without_removing_medium_zoom
    config = YAML.load_file(File.join(ROOT, "_config.yml"))

    assert_equal false, config.fetch("enable_math")
    assert_equal false, config.fetch("enable_masonry")
    assert_equal true, config.fetch("enable_medium_zoom")
  end

  def test_ci_and_devcontainer_runtime_labels_are_fixed
    WORKFLOW_PATHS.each do |path|
      load_workflow(path).fetch("jobs").each do |job_name, job|
        assert_equal "ubuntu-24.04", job.fetch("runs-on"), "#{path}:#{job_name} runner must be fixed"
      end
    end

    devcontainer_dockerfile = read(".devcontainer/Dockerfile")
    assert_match(
      /^FROM mcr\.microsoft\.com\/devcontainers\/jekyll:3\.4-bookworm@sha256:[0-9a-f]{64}$/,
      devcontainer_dockerfile
    )
    assert_match(/^FROM python:3\.13\.14-slim-bookworm@sha256:[0-9a-f]{64} AS python-runtime$/,
                 devcontainer_dockerfile)
    assert_match(/^FROM node:24\.18\.0-bookworm-slim@sha256:[0-9a-f]{64} AS node-runtime$/,
                 devcontainer_dockerfile)
    assert_match(/^FROM ruby:3\.4\.10-slim-bookworm@sha256:[0-9a-f]{64} AS ruby-runtime$/,
                 devcontainer_dockerfile)
    assert_match(/^ARG BUNDLER_VERSION=2\.6\.9$/, devcontainer_dockerfile)
    assert_match(/^ARG RUBY_VERSION=3\.4\.10$/, devcontainer_dockerfile)
    assert_includes devcontainer_dockerfile,
                    "RUBY_DOWNLOAD_URL=https://cache.ruby-lang.org/pub/ruby/3.4/ruby-3.4.10.tar.xz"
    assert_includes devcontainer_dockerfile,
                    "RUBY_DOWNLOAD_SHA256=6f32ad662baafc228d12030dbcd284f83b034dd4337b300dc84ac74d11a1eb68"
    assert_match(%r{COPY --from=ruby-runtime /usr/local /usr/local}, devcontainer_dockerfile)
    assert_match(%r{COPY --from=python-runtime /usr/local /usr/local}, devcontainer_dockerfile)
    assert_match(%r{COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node}, devcontainer_dockerfile)
    assert_includes devcontainer_dockerfile,
                    "ln -sfn ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm"
    assert_includes devcontainer_dockerfile,
                    "ln -sfn ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx"
    %w[build-essential imagemagick lsof poppler-utils procps zlib1g-dev].each do |package|
      assert_includes devcontainer_dockerfile, package
    end
    refute_match(/^\s+inotify-tools(?: \\)?$/m, devcontainer_dockerfile,
                 "force-polling Jekyll does not use the inotify-tools CLI")
    refute_match(/gem install[^\n]*bundler/i, devcontainer_dockerfile,
                 "the digest-pinned Ruby stage already supplies Bundler 2.6.9")
    assert_includes devcontainer_dockerfile, 'bundle _${BUNDLER_VERSION}_ --version'
    assert_match(%r{chown -R vscode:vscode /usr/local/bundle}, devcontainer_dockerfile)
    assert_match(%r{ENV PATH=/usr/local/bundle/bin:/usr/local/bin:}, devcontainer_dockerfile)
    assert_match(/^ENV BUNDLER_VERSION=\$\{BUNDLER_VERSION\}/, devcontainer_dockerfile)
    assert_includes devcontainer_dockerfile,
                    'test "$(ruby --disable-gems -e \'print RUBY_VERSION\')" = "${RUBY_VERSION}"'

    devcontainer = read(".devcontainer/devcontainer.json")
    refute_match(/"features"\s*:/, devcontainer)
    assert_match(/"postCreateCommand"\s*:\s*"bundle _2\.6\.9_ install --jobs 4 --retry 3 && npm ci[^"]*requirements-build\.txt[^"]*"/,
                 devcontainer)
    assert_includes devcontainer,
                    '"PATH": "/home/vscode/.local/bin:/usr/local/bundle/bin:/usr/local/bin:${containerEnv:PATH}"',
                    "user-installed Python entry points must be available in devcontainer terminals"

    smoke = read("test/devcontainer_post_create_smoke.sh")
    assert_includes smoke, 'docker build --tag "$IMAGE" --file "$ROOT/.devcontainer/Dockerfile" "$ROOT"'
    assert_includes smoke, 'DEVCONTAINER_SMOKE_REUSE_IMAGE'
    assert_includes smoke, 'docker image inspect "$IMAGE"'
    assert_includes smoke, 'index .Config.Labels "devcontainer.metadata"'
    assert_includes smoke, 'test "$DEVCONTAINER_METADATA" = "[]"'
    assert_includes smoke, "--user vscode"
    assert_includes smoke, 'test "$(cd /tmp && bundle --version)" = "Bundler version 2.6.9"'
    assert_includes smoke, "bundle _2.6.9_ install --jobs 4 --retry 3"
    assert_includes smoke, "npm ci"
    assert_includes smoke,
                    "python3 -m pip install --user --break-system-packages --require-hashes -r requirements-build.txt"
    assert_includes smoke, "bundle _2.6.9_ check"
    behavior_contract = File.join(ROOT, "test/devcontainer_start_behavior_test.rb")
    assert File.file?(behavior_contract),
           "devcontainer launcher behavior needs a dedicated test executed in the development image"
    assert_includes smoke, "bundle _2.6.9_ exec ruby test/devcontainer_start_behavior_test.rb",
                    "the development-image smoke test must exercise lsof/procps-dependent launcher behavior"

    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    %w[validate build].each do |job_name|
      smoke_step = jobs.fetch(job_name).fetch("steps").find do |step|
        step.fetch("run", "").include?("./test/devcontainer_post_create_smoke.sh")
      end
      refute_nil smoke_step, "#{job_name} must run a real devcontainer remote-user smoke test"
      assert_equal "true", smoke_step.dig("env", "DEVCONTAINER_SMOKE_REUSE_IMAGE"),
                   "#{job_name} already built the exact smoke image and must not rebuild it"
    end
  end

  def test_compose_files_share_the_fixed_local_build_target
    compose_files = %w[docker-compose.yml docker-compose-slim.yml].map do |path|
      YAML.load_file(File.join(ROOT, path))
    end
    assert_equal compose_files.first, compose_files.last

    service = compose_files.first.fetch("services").fetch("jekyll")
    assert_equal "mtics-al-folio:local", service.fetch("image")
    assert_equal ".", service.dig("build", "context")
    assert_equal "/srv/jekyll/bin/entry_point.sh", service.fetch("command")
  end

  def test_container_bundle_survives_the_source_bind_mount
    dockerfile = read("Dockerfile")
    assert_match(/BUNDLE_PATH=\/usr\/local\/bundle/, dockerfile)
  end

  def test_docker_build_context_excludes_repository_history_secrets_and_caches
    dockerignore_path = File.join(ROOT, ".dockerignore")
    assert File.file?(dockerignore_path), "local and CI image builds need an explicit context boundary"

    patterns = File.readlines(dockerignore_path).map(&:strip).reject do |line|
      line.empty? || line.start_with?("#")
    end
    %w[.git .claude .codex .playwright-cli _site .env* .jekyll-cache node_modules vendor **/__pycache__ **/*.py[cod]].each do |pattern|
      assert_includes patterns, pattern
    end
    %w[Gemfile Gemfile.lock requirements-build.txt bin/entry_point.sh].each do |required_input|
      refute_includes patterns, required_input, "#{required_input} is required by Dockerfile"
    end
  end

  def test_repository_local_agent_state_is_ignored_without_global_configuration
    gitignore = File.readlines(File.join(ROOT, ".gitignore")).map(&:strip)
    dockerignore = File.readlines(File.join(ROOT, ".dockerignore")).map(&:strip)

    %w[.claude/ .codex/ .playwright-cli/ .env* .DS_Store].each do |pattern|
      assert_includes gitignore, pattern
      assert_includes dockerignore, pattern.delete_suffix("/")
    end
  end

  def test_local_delivery_image_defaults_to_a_non_root_user_with_writable_runtime_paths
    dockerfile = read("Dockerfile")
    default_user = dockerfile.lines.reverse.find { |line| line.match?(/^USER\s+/) }

    refute_nil default_user, "the delivery image must declare a default runtime user"
    refute_match(/^USER\s+(?:root|0)(?::0)?\s*$/, default_user.to_s)
    assert_match(/ENV\s+[^\n]*HOME=\/home\/[A-Za-z0-9_-]+/m, dockerfile)
    assert_match(/chown\s+-R\s+[^\n]*\/usr\/local\/bundle[^\n]*\/home\//m, dockerfile,
                 "the default user must be able to update its bundle and home")

    %w[docker-compose.yml docker-compose-slim.yml].each do |path|
      service = YAML.load_file(File.join(ROOT, path)).fetch("services").fetch("jekyll")
      refute_equal "root", service["user"], "#{path} must not override the image back to root"
      refute_equal "0", service["user"].to_s, "#{path} must not override the image back to uid 0"
    end
  end

  def test_container_os_is_fully_upgraded_inside_the_fresh_immutable_debian_snapshot
    %w[Dockerfile .devcontainer/Dockerfile].each do |path|
      dockerfile = read(path)
      assert_match(/^ARG DEBIAN_SNAPSHOT=20260712T043000Z$/, dockerfile)
      apt_stages = dockerfile
                   .split(/^FROM /)
                   .drop(1)
                   .select { |stage| stage.include?("apt-get install -y --no-install-recommends") }
      assert_equal(path == "Dockerfile" ? 2 : 1, apt_stages.length,
                   "every intended APT stage must remain visible to the freshness contract")

      apt_stages.each_with_index do |stage, stage_index|
        label = "#{path} APT stage #{stage_index + 1}"
        assert_includes stage,
                        "https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}"
        assert_includes stage,
                        "https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}"
        assert_includes stage, 'Acquire::Check-Valid-Until "false";'

        update_index = stage.index("apt-get update")
        upgrade_index = stage.index("apt-get -y --no-install-recommends dist-upgrade")
        install_index = stage.index("apt-get install -y --no-install-recommends")
        freshness_index = stage.index("apt-get -s dist-upgrade > /tmp/apt-upgrade-plan")
        cleanup_index = stage.index("rm -rf /var/lib/apt/lists/*")

        refute_nil update_index, "#{label} must update from the pinned snapshot"
        refute_nil upgrade_index, "#{label} must upgrade every package from the pinned snapshot"
        refute_nil freshness_index, "#{label} must fail when the pinned snapshot still has upgrades"
        refute_nil cleanup_index, "#{label} must remove package-manager caches"
        assert_operator update_index, :<, upgrade_index
        assert_operator upgrade_index, :<, install_index
        assert_operator install_index, :<, freshness_index
        assert_operator freshness_index, :<, cleanup_index
        assert_includes stage, "! grep -q '^Inst ' /tmp/apt-upgrade-plan"
      end
    end
  end

  def test_release_jobs_preserve_residual_reports_and_fail_closed_on_unreviewed_or_fixable_risk
    workflow_source = read(".github/workflows/deploy.yml")
    trivy_gate = read("bin/enforce_trivy_report.py")
    trivy_image = "aquasec/trivy@sha256:be1190afcb28352bfddc4ddeb71470835d16462af68d310f9f4bca710961a41e"
    refute_includes workflow_source, "ignore-unfixed"
    refute_includes workflow_source, "aquasecurity/trivy-action@"
    assert_equal 2, workflow_source.scan("python3 test/trivy_report_contract_test.py").length
    assert_equal 2, workflow_source.scan("python3 bin/create_trivy_db_provenance.py").length
    assert_equal 1, workflow_source.scan(trivy_image).length
    workflow = load_workflow(".github/workflows/deploy.yml")
    assert_equal trivy_image, workflow.dig("env", "TRIVY_IMAGE")
    assert_match(/^EXPECTED_TRIVY_VERSION = "0\.70\.0"$/, trivy_gate)
    assert_match(/^EXPECTED_ARCHITECTURES = \("amd64", "arm64"\)$/, trivy_gate)
    assert_includes trivy_gate, "EXPECTED_RESULT_IDENTITIES"
    assert_includes trivy_gate, "package_inventory_coverage"
    assert_includes trivy_gate, "PackageInventorySHA256"
    assert_includes trivy_gate, "minimum_db_updated_at"
    assert_includes trivy_gate, "load_provenance"
    assert_includes trivy_gate, "hash_file"
    assert_includes trivy_gate, "package coverage differs from reviewed"
    assert_includes trivy_gate, "unreviewed = residuals - reviewed"
    assert_includes trivy_gate, "missing = reviewed - residuals"

    jobs = workflow.fetch("jobs")
    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      delivery_build_index = steps.index do |step|
        step.fetch("run", "").include?("docker build --tag mtics-al-folio:ci .")
      end
      development_build_index = steps.index do |step|
        step.fetch("run", "").include?(
          "docker build --tag mtics-devcontainer:ci --file .devcontainer/Dockerfile ."
        )
      end
      prepare_index = steps.index do |step|
        step.fetch("name", "") == "Prepare frozen Trivy databases"
      end
      provenance_index = steps.index do |step|
        step.fetch("name", "") == "Bind Trivy databases and reports"
      end
      first_test_index = steps.index do |step|
        step.fetch("name", "").start_with?("Verify devcontainer")
      end

      refute_nil development_build_index, "#{job_name} must build the development image before scanning it"
      refute_nil prepare_index, "#{job_name} must prepare one frozen database snapshot"
      refute_nil provenance_index, "#{job_name} must bind the reports to that snapshot"
      assert_operator delivery_build_index, :<, prepare_index
      assert_operator development_build_index, :<, prepare_index

      prepare_command = steps.fetch(prepare_index).fetch("run")
      assert_includes prepare_command, 'rm -rf "$RUNNER_TEMP/trivy-cache" "$RUNNER_TEMP/trivy-reports"'
      assert_equal 1, prepare_command.scan("--download-db-only").length
      assert_equal 1, prepare_command.scan("--download-java-db-only").length
      assert_includes prepare_command, 'mkdir -p "$RUNNER_TEMP/trivy-cache" "$RUNNER_TEMP/trivy-reports"'
      assert_includes prepare_command, "docker buildx imagetools inspect --raw ghcr.io/aquasecurity/trivy-db:2"
      assert_includes prepare_command, "docker buildx imagetools inspect --raw ghcr.io/aquasecurity/trivy-java-db:1"
      assert_equal 2, prepare_command.scan("python3 bin/validate_trivy_oci_manifest.py").length
      assert_includes prepare_command, '--database vulnerability'
      assert_includes prepare_command, '--database java'
      assert_includes prepare_command, '--db-repository "ghcr.io/aquasecurity/trivy-db@$vulnerability_db_digest"'
      assert_includes prepare_command, '--java-db-repository "ghcr.io/aquasecurity/trivy-java-db@$java_db_digest"'
      assert_includes prepare_command, "--no-progress"
      assert_includes prepare_command, "version --cache-dir /trivy-cache --format json"
      assert_equal 3, prepare_command.scan('"$TRIVY_IMAGE"').length
      assert_includes prepare_command, "$RUNNER_TEMP/trivy-version.json"

      scan_indices = %w[delivery development].map do |label|
        steps.index { |step| step.fetch("name", "") == "Scan #{label} container" }
      end
      refute_includes scan_indices, nil

      %w[delivery development].zip(scan_indices).each do |label, scan_index|
        scan = steps.fetch(scan_index)
        assert_equal "${{ success() && !cancelled() }}", scan.fetch("if")
        scan_command = scan.fetch("run")
        image_ref = label == "delivery" ? "mtics-al-folio:ci" : "mtics-devcontainer:ci"
        assert_equal 1, scan_command.scan('"$TRIVY_IMAGE"').length
        assert_includes scan_command, '--volume /var/run/docker.sock:/var/run/docker.sock:ro'
        assert_includes scan_command, '--volume "$RUNNER_TEMP/trivy-cache:/trivy-cache:ro"'
        assert_includes scan_command, '--volume "$RUNNER_TEMP/trivy-reports:/trivy-reports"'
        assert_includes scan_command, "--cache-dir /trivy-cache"
        assert_includes scan_command, "--cache-backend memory"
        assert_includes scan_command, "--skip-db-update"
        assert_includes scan_command, "--skip-java-db-update"
        assert_includes scan_command, "--skip-version-check"
        assert_includes scan_command, "--offline-scan"
        assert_includes scan_command, "--disable-telemetry"
        assert_includes scan_command, "--image-src docker"
        assert_includes scan_command, "--no-progress"
        assert_includes scan_command, "--scanners vuln"
        assert_includes scan_command, "--pkg-types os,library"
        assert_includes scan_command, "--severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL"
        assert_includes scan_command, "--list-all-pkgs"
        assert_includes scan_command, "--format json"
        assert_includes scan_command, "--output /trivy-reports/trivy-#{label}.json"
        assert_includes scan_command, image_ref
        refute_includes scan_command, "--ignore-unfixed"
        assert_operator prepare_index, :<, scan_index
        assert_operator scan_index, :<, provenance_index
      end

      provenance = steps.fetch(provenance_index)
      assert_equal "${{ success() && !cancelled() }}", provenance.fetch("if")
      provenance_command = provenance.fetch("run")
      assert_includes provenance_command, "python3 bin/create_trivy_db_provenance.py"
      assert_includes provenance_command, '--trivy-version-json "$RUNNER_TEMP/trivy-version.json"'
      assert_includes provenance_command, '--vulnerability-db "$RUNNER_TEMP/trivy-cache/db/trivy.db"'
      assert_includes provenance_command, '--vulnerability-db-metadata "$RUNNER_TEMP/trivy-cache/db/metadata.json"'
      assert_includes provenance_command, '--java-db "$RUNNER_TEMP/trivy-cache/java-db/trivy-java.db"'
      assert_includes provenance_command, '--java-db-metadata "$RUNNER_TEMP/trivy-cache/java-db/metadata.json"'
      assert_includes provenance_command, '--vulnerability-db-manifest "$RUNNER_TEMP/trivy-reports/trivy-vulnerability-db-manifest.json"'
      assert_includes provenance_command, '--java-db-manifest "$RUNNER_TEMP/trivy-reports/trivy-java-db-manifest.json"'
      assert_includes provenance_command, "--expected-architecture amd64"
      assert_includes provenance_command, '--delivery-report "$RUNNER_TEMP/trivy-reports/trivy-delivery.json"'
      assert_includes provenance_command, '--development-report "$RUNNER_TEMP/trivy-reports/trivy-development.json"'
      assert_includes provenance_command, '--output "$RUNNER_TEMP/trivy-reports/trivy-db-provenance.json"'

      %w[delivery development].each do |label|
        gate_index = steps.index do |step|
          step.fetch("name", "") == "Enforce reviewed #{label} vulnerability baseline"
        end
        refute_nil gate_index
        gate = steps.fetch(gate_index)
        assert_equal "Enforce reviewed #{label} vulnerability baseline", gate.fetch("name")
        assert_equal "${{ success() && !cancelled() }}", gate.fetch("if")
        gate_command = gate.fetch("run")
        assert_includes gate_command, '--volume "$RUNNER_TEMP/trivy-reports:/trivy-reports:ro"'
        assert_includes gate_command, '--volume "$RUNNER_TEMP/trivy-cache:/trivy-cache:ro"'
        assert_includes gate_command, '--volume "$PWD:/srv/jekyll:ro"'
        assert_includes gate_command, "docker run --rm --pull never"
        assert_includes gate_command, "python3 bin/enforce_trivy_report.py"
        assert_includes gate_command, "--report /trivy-reports/trivy-#{label}.json"
        assert_includes gate_command, "--baseline .trivy-unfixed-baseline.json"
        assert_includes gate_command, "--provenance /trivy-reports/trivy-db-provenance.json"
        assert_includes gate_command, "--vulnerability-db /trivy-cache/db/trivy.db"
        assert_includes gate_command, "--vulnerability-db-metadata /trivy-cache/db/metadata.json"
        assert_includes gate_command, "--java-db /trivy-cache/java-db/trivy-java.db"
        assert_includes gate_command, "--java-db-metadata /trivy-cache/java-db/metadata.json"
        assert_includes gate_command, "--vulnerability-db-manifest /trivy-reports/trivy-vulnerability-db-manifest.json"
        assert_includes gate_command, "--java-db-manifest /trivy-reports/trivy-java-db-manifest.json"
        assert_includes gate_command, "--expected-architecture amd64"
        assert_includes gate_command, "--image #{label}"
        assert_operator provenance_index, :<, gate_index
        assert_operator gate_index, :<, first_test_index,
                        "#{job_name} must gate both reports before any downstream test"
      end

      %w[delivery development].each do |label|
        upload = steps.find do |step|
          step.dig("with", "name") == "trivy-#{label}-#{job_name}"
        end
        refute_nil upload
        assert_equal "always()", upload.fetch("if")
        assert_equal "${{ runner.temp }}/trivy-reports/trivy-#{label}.json", upload.dig("with", "path")
        assert_equal "error", upload.dig("with", "if-no-files-found")
      end

      provenance_upload = steps.find do |step|
        step.dig("with", "name") == "trivy-db-provenance-#{job_name}"
      end
      refute_nil provenance_upload
      assert_equal "always()", provenance_upload.fetch("if")
      provenance_paths = provenance_upload.dig("with", "path")
      assert_includes provenance_paths, "${{ runner.temp }}/trivy-reports/trivy-db-provenance.json"
      assert_includes provenance_paths, "${{ runner.temp }}/trivy-reports/trivy-vulnerability-db-manifest.json"
      assert_includes provenance_paths, "${{ runner.temp }}/trivy-reports/trivy-java-db-manifest.json"
      assert_equal "error", provenance_upload.dig("with", "if-no-files-found")
    end
  end

  def test_npm_is_replaced_by_a_checksum_pinned_consistent_fixed_release
    %w[Dockerfile .devcontainer/Dockerfile].each do |path|
      dockerfile = read(path)
      checksum_add = <<~DOCKERFILE.strip
        ADD --checksum=sha256:73f6155215ebabf4ed96dca1f567c2372cc713c33af2e5b9b62fde4e92373e2e https://registry.npmjs.org/npm/-/npm-11.18.0.tgz /tmp/npm.tgz
      DOCKERFILE

      assert_includes dockerfile, checksum_add,
                      "#{path} must authenticate the complete fixed npm distribution"
      assert_match(/^ARG NPM_VERSION=11\.18\.0$/, dockerfile)
      refute_includes dockerfile,
                      "COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm"
      assert_includes dockerfile,
                      "rm -rf /usr/local/lib/node_modules/npm"
      assert_includes dockerfile,
                      "tar -xzf /tmp/npm.tgz --strip-components=1 -C /usr/local/lib/node_modules/npm"
      assert_includes dockerfile, 'test "$(npm --version)" = "${NPM_VERSION}"'
      assert_includes dockerfile,
                      'require("/usr/local/lib/node_modules/npm/node_modules/undici/package.json").version'
      assert_includes dockerfile, '"6.27.0"'
      assert_operator dockerfile.index(checksum_add), :<, dockerfile.index("rm -rf /usr/local/lib/node_modules/npm")
    end
  end

  def test_devcontainer_removes_stale_ruby_library_metadata_before_overlaying_the_pinned_runtime
    dockerfile = read(".devcontainer/Dockerfile")
    cleanup_index = dockerfile.index("RUN rm -rf /usr/local/lib/ruby /usr/local/rvm")
    ruby_copy_index = dockerfile.index("COPY --from=ruby-runtime /usr/local /usr/local")

    refute_nil cleanup_index, "the MCR base must not retain either stale Ruby installation beneath COPY"
    assert_operator cleanup_index, :<, ruby_copy_index
    assert_includes dockerfile, 'LABEL devcontainer.metadata="[]"',
                    "the derived image must erase inherited network-running lifecycle hooks"
    refute_includes dockerfile, "/usr/local/post-create.sh"
  end

  def test_trivy_baseline_commits_to_findings_and_multi_arch_package_coverage
    baseline = JSON.parse(read(".trivy-unfixed-baseline.json"))
    assert_equal 4, baseline.fetch("schema_version")
    assert_equal %w[coverage images minimum_db_updated_at review_before reviewed_at schema_version vulnerability_coverage],
                 baseline.keys.sort
    minimum_db_updated_at = baseline.fetch("minimum_db_updated_at")
    assert_equal %w[java vulnerability], minimum_db_updated_at.keys.sort
    minimum_db_updated_at.each_value do |timestamp|
      assert_match(/\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\z/, timestamp)
      DateTime.iso8601(timestamp)
    end
    assert_equal %w[delivery development], baseline.fetch("images").keys.sort
    assert_equal %w[delivery development], baseline.fetch("coverage").keys.sort
    assert_equal %w[delivery development], baseline.fetch("vulnerability_coverage").keys.sort

    expected_identities = [
      ["lang-pkgs", "gemspec"],
      ["lang-pkgs", "node-pkg"],
      ["lang-pkgs", "python-pkg"],
      ["os-pkgs", "debian"]
    ]
    baseline.fetch("coverage").each do |image, architectures|
      assert_equal %w[amd64 arm64], architectures.keys.sort
      architectures.each do |architecture, entries|
        assert_equal expected_identities,
                     entries.map { |entry| [entry.fetch("Class"), entry.fetch("Type")] },
                     "#{image}/#{architecture} must commit every scanner result"
        entries.each do |entry|
          assert_equal %w[Class PackageCount PackageInventorySHA256 Type], entry.keys.sort
          assert_operator entry.fetch("PackageCount"), :>, 0
          assert_match(/\A[0-9a-f]{64}\z/, entry.fetch("PackageInventorySHA256"))
        end
      end
    end

    baseline.fetch("vulnerability_coverage").each do |image, architectures|
      assert_equal %w[amd64 arm64], architectures.keys.sort
      architectures.each do |architecture, entries|
        assert_equal %w[CRITICAL HIGH LOW MEDIUM UNKNOWN],
                     entries.map { |entry| entry.fetch("Severity") },
                     "#{image}/#{architecture} must prove every severity was retained"
        entries.each do |entry|
          assert_equal %w[FindingCount FindingInventorySHA256 Severity], entry.keys.sort
          assert_operator entry.fetch("FindingCount"), :>=, 0
          assert_match(/\A[0-9a-f]{64}\z/, entry.fetch("FindingInventorySHA256"))
        end
        blocking_count = entries
                         .select { |entry| %w[CRITICAL HIGH].include?(entry.fetch("Severity")) }
                         .sum { |entry| entry.fetch("FindingCount") }
        assert_equal baseline.dig("images", image).length, blocking_count
      end
    end
  end

  def test_trivy_review_manifest_binds_the_generated_baseline_and_four_reports
    baseline_path = File.join(ROOT, ".trivy-unfixed-baseline.json")
    baseline_bytes = File.binread(baseline_path)
    baseline = JSON.parse(baseline_bytes)
    manifest = JSON.parse(read(".trivy-baseline-review.json"))

    assert_equal 1, manifest.fetch("schema_version")
    assert_equal %w[
      baseline databases high_critical_inventory_sha256 reports scanner schema_version
      trivy_version_json_sha256
    ], manifest.keys.sort
    assert_equal(
      {
        "path" => ".trivy-unfixed-baseline.json",
        "schema_version" => 4,
        "sha256" => Digest::SHA256.hexdigest(baseline_bytes)
      },
      manifest.fetch("baseline")
    )
    assert_match(/\A[0-9a-f]{64}\z/, manifest.fetch("trivy_version_json_sha256"))

    scanner = manifest.fetch("scanner")
    assert_equal "Trivy", scanner.fetch("name")
    assert_equal "0.70.0", scanner.fetch("version")
    assert_equal(
      "aquasec/trivy@sha256:be1190afcb28352bfddc4ddeb71470835d16462af68d310f9f4bca710961a41e",
      scanner.fetch("container_image")
    )
    assert_equal(
      {
        "scanners" => ["vuln"],
        "pkg_types" => %w[os library],
        "severities" => %w[UNKNOWN LOW MEDIUM HIGH CRITICAL],
        "list_all_packages" => true,
        "offline_scan" => true,
        "skip_db_update" => true,
        "skip_java_db_update" => true
      },
      scanner.fetch("scan_profile")
    )

    databases = manifest.fetch("databases")
    assert_equal %w[java vulnerability], databases.keys.sort
    {"vulnerability" => 2, "java" => 1}.each do |database_name, schema_version|
      database = databases.fetch(database_name)
      assert_equal %w[
        downloaded_at metadata_sha256 next_update oci schema_version sha256 updated_at
      ], database.keys.sort
      assert_equal schema_version, database.fetch("schema_version")
      assert_equal baseline.dig("minimum_db_updated_at", database_name),
                   database.fetch("updated_at")
      %w[updated_at next_update downloaded_at].each do |field|
        DateTime.iso8601(database.fetch(field))
      end
      assert_operator DateTime.iso8601(database.fetch("updated_at")), :<,
                      DateTime.iso8601(database.fetch("next_update"))
      assert_match(/\A[0-9a-f]{64}\z/, database.fetch("sha256"))
      assert_match(/\A[0-9a-f]{64}\z/, database.fetch("metadata_sha256"))
      oci = database.fetch("oci")
      assert_equal %w[
        layer_digest layer_media_type layer_size manifest_digest repository resolved_from
      ], oci.keys.sort
      expected_oci = if database_name == "vulnerability"
                       {
                         "repository" => "ghcr.io/aquasecurity/trivy-db",
                         "resolved_from" => "ghcr.io/aquasecurity/trivy-db:2",
                         "layer_media_type" => "application/vnd.aquasec.trivy.db.layer.v1.tar+gzip"
                       }
                     else
                       {
                         "repository" => "ghcr.io/aquasecurity/trivy-java-db",
                         "resolved_from" => "ghcr.io/aquasecurity/trivy-java-db:1",
                         "layer_media_type" => "application/vnd.aquasec.trivy.javadb.layer.v1.tar+gzip"
                       }
                     end
      expected_oci.each { |field, value| assert_equal value, oci.fetch(field) }
      assert_match(/\Asha256:[0-9a-f]{64}\z/, oci.fetch("manifest_digest"))
      assert_match(/\Asha256:[0-9a-f]{64}\z/, oci.fetch("layer_digest"))
      assert_operator oci.fetch("layer_size"), :>, 0
    end

    expected_artifacts = {
      ["delivery", "amd64"] => "mtics-al-folio:ci",
      ["delivery", "arm64"] => "mtics-al-folio:release-arm64",
      ["development", "amd64"] => "mtics-devcontainer:ci",
      ["development", "arm64"] => "mtics-devcontainer:release-arm64"
    }
    reports = manifest.fetch("reports")
    assert_equal %w[delivery development], reports.keys.sort
    reports.each do |image, architectures|
      assert_equal %w[amd64 arm64], architectures.keys.sort
      architectures.each do |architecture, report|
        assert_equal %w[
          architecture artifact_name created_at fixable_high_critical_count image_id
          severity_counts sha256
        ], report.keys.sort
        assert_equal expected_artifacts.fetch([image, architecture]),
                     report.fetch("artifact_name")
        assert_equal architecture, report.fetch("architecture")
        assert_match(/\Asha256:[0-9a-f]{64}\z/, report.fetch("image_id"))
        assert_match(/\A[0-9a-f]{64}\z/, report.fetch("sha256"))
        assert_equal 0, report.fetch("fixable_high_critical_count")
        created_at = DateTime.iso8601(report.fetch("created_at"))
        databases.each_value do |database|
          assert_operator created_at, :>=, DateTime.iso8601(database.fetch("downloaded_at"))
          assert_operator created_at, :<, DateTime.iso8601(database.fetch("next_update"))
        end
        coverage = baseline.dig("vulnerability_coverage", image, architecture)
        expected_counts = coverage.to_h do |entry|
          [entry.fetch("Severity"), entry.fetch("FindingCount")]
        end
        assert_equal expected_counts, report.fetch("severity_counts")
      end
    end

    manifest.fetch("high_critical_inventory_sha256").each do |image, digest|
      entries = baseline.dig("images", image)
      canonical_rows = entries.map do |entry|
        %w[Class Type PkgID PkgName VulnerabilityID InstalledVersion Severity Status]
          .map { |field| entry.fetch(field) }
      end.sort
      assert_equal Digest::SHA256.hexdigest(JSON.generate(canonical_rows)), digest

      %w[CRITICAL HIGH].each do |severity|
        inventory = entries
                    .select { |entry| entry.fetch("Severity") == severity }
                    .map do |entry|
          %w[Class Type PkgID PkgName VulnerabilityID InstalledVersion Severity Status]
            .map { |field| entry.fetch(field) } + [""]
        end.sort
        expected_digest = Digest::SHA256.hexdigest(JSON.generate(inventory))
        expected_count = inventory.length
        %w[amd64 arm64].each do |architecture|
          coverage_entry = baseline
                           .dig("vulnerability_coverage", image, architecture)
                           .find { |entry| entry.fetch("Severity") == severity }
          assert_equal expected_count, coverage_entry.fetch("FindingCount")
          assert_equal expected_digest, coverage_entry.fetch("FindingInventorySHA256")
        end
      end
    end
  end

  def test_chromium_scanner_gap_is_explicit_short_lived_and_bound_to_the_image
    gap_document = YAML.load_file(File.join(ROOT, ".security-scanner-gaps.yml"))
    assert_equal 1, gap_document.fetch("schema_version")
    assert_equal %w[gaps scanner schema_version], gap_document.keys.sort
    assert_equal(
      {
        "name" => "Trivy",
        "version" => "0.70.0",
        "severities" => %w[UNKNOWN LOW MEDIUM HIGH CRITICAL],
        "package_types" => %w[os library],
        "vulnerability_db_updated_at" => "2026-07-12T07:28:36.403115102Z"
      },
      gap_document.fetch("scanner")
    )
    assert_equal 1, gap_document.fetch("gaps").length
    gap = gap_document.fetch("gaps").then { |gaps| gaps.fetch(0) }
    assert_equal %w[
      containment cves gap_type image installed_version official_source_url package
      review_before reviewed_at scanner_evidence status upstream_fixed_version
    ], gap.keys.sort

    assert_equal "chromium", gap.fetch("package")
    assert_equal "150.0.7871.114-1~deb12u1", gap.fetch("installed_version")
    assert_equal "150.0.7871.115", gap.fetch("upstream_fixed_version")
    assert_equal "delivery", gap.fetch("image")
    assert_equal "unresolved_severity_classification", gap.fetch("gap_type")
    assert_equal(
      {
        "severity" => "UNKNOWN",
        "status" => "affected",
        "fixed_version" => nil,
        "packages" => %w[chromium chromium-common],
        "unique_cves" => 27,
        "package_rows" => {"amd64" => 54, "arm64" => 54}
      },
      gap.fetch("scanner_evidence")
    )
    assert_equal "https://security-tracker.debian.org/tracker/source-package/chromium",
                 gap.fetch("official_source_url")

    cves = gap.fetch("cves")
    expected_cves = (15_107..15_133).map { |number| "CVE-2026-#{number}" }
    assert_equal expected_cves, cves
    assert_equal cves.sort, cves
    assert_equal cves.uniq, cves

    reviewed_at = Date.iso8601(gap.fetch("reviewed_at"))
    review_before = Date.iso8601(gap.fetch("review_before"))
    assert_operator reviewed_at, :<=, Date.today
    assert_operator review_before, :>, Date.today
    assert_operator (review_before - reviewed_at).to_i, :<=, 30
    assert_equal "read_only", gap.dig("containment", "browser_workspace")
    assert_equal "runner_temp", gap.dig("containment", "browser_artifacts")

    dockerfile = read("Dockerfile")
    assert_match(
      /^ARG CHROMIUM_VERSION=#{Regexp.escape(gap.fetch("installed_version"))}$/,
      dockerfile
    )
    workflow = read(".github/workflows/deploy.yml")
    assert_includes workflow,
                    "TRIVY_IMAGE: aquasec/trivy@sha256:be1190afcb28352bfddc4ddeb71470835d16462af68d310f9f4bca710961a41e # v#{gap_document.dig('scanner', 'version')}"
  end

  def test_ci_builds_and_runs_the_same_pinned_container_as_local_delivery
    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")

    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      container_build = steps.find { |step| step.fetch("run", "").include?("docker build --tag mtics-al-folio:ci .") }
      refute_nil container_build, "#{job_name} must build the checked-in Dockerfile"

      setup_actions = steps.map do |step|
        action = step.fetch("uses", "")
        action if action.start_with?("ruby/setup-ruby@", "actions/setup-node@", "actions/setup-python@")
      end.compact
      assert_empty setup_actions, "#{job_name} must not create a second host toolchain"

      host_installs = steps.select do |step|
        step.fetch("run", "").match?(/(?:apt-get|pip install|bundle install)/)
      end
      assert_empty host_installs, "#{job_name} must not resolve dependencies on the hosted runner"

      container_commands = steps.select do |step|
        step.fetch("run", "").match?(/(?:release_contract|overrides audit|bundle exec jekyll build|search_contract)/)
      end
      refute_empty container_commands
      container_commands.each do |step|
        assert_includes step.fetch("run"), "docker run --rm", "#{job_name} command escaped the pinned container"
        assert_includes step.fetch("run"), '--user "$(id -u):$(id -g)"',
                        "#{job_name} must preserve checkout ownership inside the container"
        assert_includes step.fetch("run"), "--env HOME=/tmp",
                        "#{job_name} non-root containers need a writable home"
      end

      shell_commands = steps.select { |step| step.fetch("run", "").include?("bash -lc") }
      refute_empty shell_commands
      shell_commands.each do |step|
        assert_includes step.fetch("run"), "set -euo pipefail",
                        "#{job_name} must not mask an earlier containerized test failure"
      end
    end
  end

  def test_ci_runs_each_deterministic_regression_suite_explicitly
    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    before_build = [
      "bundle exec ruby test/cache_bust_contract_test.rb",
      "bundle exec ruby test/cv_schema_rendering_contract_test.rb"
    ]
    after_build = ["bundle exec ruby test/frontend_theme_accessibility_contract_20260712_test.rb"]

    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      site_build_index = steps.index { |step| step.fetch("run", "").include?("bundle exec jekyll build") }
      refute_nil site_build_index

      before_build.each do |command|
        index = steps.index { |step| step.fetch("run", "").include?(command) }
        refute_nil index, "#{job_name} must run #{command}"
        assert_operator index, :<, site_build_index
      end
      render_step = steps.find { |step| step.fetch("run", "").include?("rendercv_inputs=(") }
      refute_nil render_step, "#{job_name} must formally render the current CV and every RenderCV fixture"
      assert_includes render_step.fetch("run"),
                      "rendercv_inputs=(_data/cv.yml test/fixtures/cv_rendercv_*.yml)"
      assert_includes render_step.fetch("run"), 'for rendercv_input in "${rendercv_inputs[@]}"'
      assert_includes render_step.fetch("run"), 'rendercv render "$rendercv_input"'
      assert_includes render_step.fetch("run"), "mktemp -d"
      assert_includes render_step.fetch("run"), '--output-folder "$rendercv_output"'
      assert_includes render_step.fetch("run"), "-name '*.pdf' -size +0c"
      after_build.each do |command|
        index = steps.index { |step| step.fetch("run", "").include?(command) }
        refute_nil index, "#{job_name} must run #{command}"
        assert_operator site_build_index, :<, index
      end

      refute steps.any? { |step| step.fetch("run", "").match?(%r{test/\*\.rb}) },
             "#{job_name} must opt deterministic suites in explicitly"
    end
  end

  def test_pull_requests_build_without_deploying_pages
    workflow = load_workflow(".github/workflows/deploy.yml")
    triggers = workflow.fetch("on")
    assert triggers.key?("pull_request"), "deploy workflow must validate pull requests"

    deploy_job = workflow.fetch("jobs").fetch("deploy")
    condition = deploy_job.fetch("if")
    assert_includes condition, "github.event_name != 'pull_request'"
  end

  def test_pull_request_validation_is_isolated_from_pages_permissions
    workflow = load_workflow(".github/workflows/deploy.yml")
    jobs = workflow.fetch("jobs")
    assert jobs.key?("validate"), "pull requests need a dedicated validation job"

    validation_job = jobs.fetch("validate")
    assert_equal({ "contents" => "read" }, validation_job.fetch("permissions"))
    assert_includes validation_job.fetch("if"), "github.event_name == 'pull_request'"

    validation_actions = validation_job.fetch("steps").map { |step| step["uses"] }.compact
    refute validation_actions.any? { |action| action.start_with?("actions/configure-pages@") }
    refute validation_actions.any? { |action| action.start_with?("actions/upload-pages-artifact@") }
    refute validation_actions.any? { |action| action.start_with?("actions/deploy-pages@") }

    pages_build = jobs.fetch("build")
    assert_equal "read", pages_build.dig("permissions", "contents")
    assert_equal "read", pages_build.dig("permissions", "pages")
    refute pages_build.fetch("permissions").key?("id-token")
    assert_includes pages_build.fetch("if"), "github.event_name != 'pull_request'"

    deploy_job = jobs.fetch("deploy")
    assert_equal "write", deploy_job.dig("permissions", "pages")
    assert_equal "write", deploy_job.dig("permissions", "id-token")
  end

  def test_search_contract_runs_after_each_site_build
    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    command = "node --test test/search_contract_test.mjs"
    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      site_build_index = steps.index { |step| step.fetch("run", "").include?("bundle exec jekyll build") }
      search_index = steps.index { |step| step.fetch("run", "").include?(command) }
      refute_nil search_index, "#{job_name} must run the search contract"
      assert_operator site_build_index, :<, search_index,
                      "#{job_name} must build the site before running rendered-site contracts"
    end
  end

  def test_minimal_runtime_contract_runs_after_each_site_build
    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    command = "python3 test/minimal_runtime_contract_test.py"

    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      build_index = steps.index { |step| step.fetch("run", "").include?("bundle exec jekyll build") }
      contract_index = steps.index { |step| step.fetch("run", "").include?(command) }
      refute_nil contract_index, "#{job_name} must reject unused global runtimes in built HTML"
      assert_operator build_index, :<, contract_index
    end
  end

  def test_accessibility_contract_runs_after_each_site_build_and_before_upload
    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    command = "python3 test/accessibility_contract_test.py"
    job_names = %w[validate build]
    missing = job_names.reject do |job_name|
      jobs.fetch(job_name).fetch("steps").any? { |step| step.fetch("run", "").include?(command) }
    end
    assert_empty missing, "static accessibility contract missing from jobs: #{missing.join(', ')}"

    job_names.each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      site_build_index = steps.index { |step| step.fetch("run", "").include?("bundle exec jekyll build") }
      accessibility_index = steps.index { |step| step.fetch("run", "").include?(command) }
      refute_nil site_build_index, "#{job_name} must build the site"
      assert_operator site_build_index, :<, accessibility_index,
                      "#{job_name} must build _site before checking accessibility"

      upload_index = steps.index do |step|
        step.fetch("uses", "").start_with?("actions/upload-pages-artifact@")
      end
      next unless upload_index

      assert_operator accessibility_index, :<, upload_index,
                      "#{job_name} must check accessibility before uploading _site"
    end
  end

  def test_pinned_browser_contracts_run_after_each_site_build
    dockerfile = read("Dockerfile")
    assert_match(/^ARG CHROMIUM_VERSION=150\.0\.7871\.114-1~deb12u1$/, dockerfile)
    assert_includes dockerfile, 'chromium="${CHROMIUM_VERSION}"'
    assert_match(/^playwright==1\.61\.0\b/, read("requirements-build.txt"))

    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      build_index = steps.index { |step| step.fetch("run", "").include?("bundle exec jekyll build") }
      browser_index = steps.index do |step|
        run = step.fetch("run", "")
        run.include?("python3 test/accessibility_browser_test.py") &&
          run.include?("python3 test/frontend_theme_browser_interactions_20260712_test.py")
      end

      refute_nil browser_index, "#{job_name} must run both committed browser suites"
      assert_operator build_index, :<, browser_index
      browser_step = steps.fetch(browser_index)
      assert_includes browser_step.fetch("run"), "python3 -m http.server 8091 --directory _site"
      assert_includes browser_step.fetch("run"), "A11Y_ARTIFACT_DIR=/a11y-artifacts"
      assert_includes browser_step.fetch("run"), "a11y-#{job_name}:/a11y-artifacts"
      assert_includes browser_step.fetch("run"), '--volume "$PWD:/srv/jekyll:ro"',
                      "the browser-facing container must not mutate the Pages artifact source"
      assert_equal 1, browser_step.fetch("run").scan('--volume "$PWD:/srv/jekyll:ro"').length,
                   "each browser container needs exactly one workspace mount"
      refute_match(/--volume "\$PWD:\/srv\/jekyll"(?:\s|\\)/, browser_step.fetch("run"))
      assert_equal "/usr/bin/chromium", browser_step.dig("env", "CHROME_EXECUTABLE")

      browser_upload_index = steps.index do |step|
        step.fetch("name", "") == "Upload browser audit artifacts"
      end
      assert_operator browser_index, :<, browser_upload_index
      browser_upload = steps.fetch(browser_upload_index)
      assert_equal "always()", browser_upload.fetch("if")
      assert_equal "browser-audit-#{job_name}", browser_upload.dig("with", "name")

      upload_index = steps.index { |step| step.fetch("uses", "").start_with?("actions/upload-pages-artifact@") }
      assert_operator browser_index, :<, upload_index if upload_index
    end

    browser_source = read("test/accessibility_browser_test.py")
    assert_includes browser_source, 'ARTIFACT_DIR / f"cv-{label}.png"'
    assert_includes browser_source, '("mobile", {"width": 390, "height": 844})'
  end

  def test_browser_audit_verifies_the_pinned_axe_payload_before_execution
    source = read("test/accessibility_browser_test.py")

    assert_includes source, 'AXE_SHA256 = "880970c081707360e64f34cea25ff91892f5bc95675b0776925b9709dd8a68bb"'
    assert_match(/hashlib\.sha256\(axe_payload\)\.hexdigest\(\)/, source)
    assert_match(/axe_digest != AXE_SHA256/, source)
    assert_operator source.index("axe_digest != AXE_SHA256"), :<, source.index("axe_payload.decode")
    assert_operator source.index("axe_payload.decode"), :<, source.index("violations = run_axe(page, axe_source)")
  end

  def test_social_contract_runs_with_the_bundled_liquid_runtime
    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    command = "bundle exec ruby test/accessibility_social_contract_test.rb"

    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      social_index = steps.index { |step| step.fetch("run", "").include?(command) }
      site_build_index = steps.index { |step| step.fetch("run", "").include?("bundle exec jekyll build") }

      refute_nil social_index, "#{job_name} must run the social Liquid contract through Bundler"
      assert_operator social_index, :<, site_build_index,
                      "#{job_name} must reject unsafe social data before building the site"
    end
  end

  def test_release_and_override_audits_run_before_each_site_build
    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    audit_command = "bundle exec al-folio upgrade overrides audit --fail-on-stale"

    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      release_index = steps.index { |step| step.fetch("run", "").include?("ruby test/release_contract_test.rb") }
      audit_index = steps.index { |step| step.fetch("run", "").include?(audit_command) }
      build_index = steps.index { |step| step.fetch("run", "").include?("bundle exec jekyll build") }

      refute_nil release_index, "#{job_name} must run release contracts"
      assert_equal "1", steps.fetch(release_index).dig("env", "VERIFY_GIT_INDEX")
      refute_nil audit_index, "#{job_name} must audit al-folio overrides"
      assert_operator audit_index, :<, build_index
    end
  end

  def test_feed_timestamp_is_derived_from_the_checked_out_revision
    config = YAML.load_file(File.join(ROOT, "_config.yml"))
    refute config.key?("time"), "a wall-clock-independent timestamp must be supplied at build time"

    jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    %w[validate build].each do |job_name|
      steps = jobs.fetch(job_name).fetch("steps")
      config_index = steps.index do |step|
        run = step.fetch("run", "")
        run.include?("git show -s --format=%cI HEAD") && run.include?("> .jekyll-reproducible.yml")
      end
      build_index = steps.index { |step| step.fetch("run", "").include?("bundle exec jekyll build") }

      refute_nil config_index, "#{job_name} must derive site.time from the commit"
      assert_operator config_index, :<, build_index
      assert_includes steps.fetch(build_index).fetch("run"),
                      "--config _config.yml,.jekyll-reproducible.yml"
      refute_includes steps.fetch(build_index).fetch("run"), "$RUNNER_TEMP"
    end
  end

  def test_regression_sources_are_excluded_from_the_pages_artifact
    config = YAML.load_file(File.join(ROOT, "_config.yml"))
    excluded_paths = Array(config.fetch("exclude"))

    assert_includes excluded_paths, "test/",
                    "test sources and local browser paths must not be published in _site"
    %w[
      requirements.txt
      requirements-build.in
      requirements-build.txt
      requirements-citations.in
      requirements-citations.txt
      .jekyll-reproducible.yml
    ].each do |path|
      assert_includes excluded_paths, path, "#{path} must not be published in _site"
    end
  end

  def test_citation_refresh_uses_off_hour_daily_schedule_and_fixed_concurrency
    workflow = load_workflow(".github/workflows/update_scholar_citations.yml")
    triggers = workflow.fetch("on")

    assert_equal ["17 8 * * *"], triggers.fetch("schedule").map { |entry| entry.fetch("cron") }
    assert triggers.key?("workflow_dispatch"), "citation refresh must remain manually dispatchable"
    assert_equal({ "group" => "scholar-citations", "cancel-in-progress" => false },
                 workflow.fetch("concurrency"))
  end

  def test_citation_refresh_jobs_have_bounded_timeouts
    jobs = load_workflow(".github/workflows/update_scholar_citations.yml").fetch("jobs")

    assert_equal 15, jobs.fetch("update").fetch("timeout-minutes")
    assert_equal 5, jobs.fetch("commit").fetch("timeout-minutes")
  end

  def test_citation_refresh_isolates_secreted_updates_from_write_credentials
    jobs = load_workflow(".github/workflows/update_scholar_citations.yml").fetch("jobs")
    update = jobs.fetch("update")
    commit = jobs["commit"]
    refute_nil commit, "citation refresh must publish through an isolated write-only job"
    return unless commit

    assert_equal({ "contents" => "read" }, update.fetch("permissions"))
    assert_equal({ "contents" => "write" }, commit.fetch("permissions"))
    assert_equal "update", commit.fetch("needs")
    assert_includes update.to_s, "SERPAPI_API_KEY"
    refute_includes update.to_s, "ALLOW_CITATION_KEY_DELETION",
                    "scheduled citation refreshes must not pre-authorize key deletion"
    refute_includes commit.to_s, "SERPAPI_API_KEY"

    update_steps = update.fetch("steps")
    update_checkout = update_steps.find { |step| step.fetch("uses", "").start_with?("actions/checkout@") }
    setup_python = update_steps.find { |step| step.fetch("uses", "").start_with?("actions/setup-python@") }
    install = update_steps.find { |step| step.fetch("run", "").include?("requirements-citations.txt") }
    upload = update_steps.find { |step| step.fetch("uses", "").start_with?("actions/upload-artifact@") }
    assert_equal false, update_checkout.dig("with", "persist-credentials")
    assert_equal "3.13.14", setup_python.dig("with", "python-version")
    assert_includes install.fetch("run"), "--require-hashes -r requirements-citations.txt"
    assert_equal "scholar-citations", upload.dig("with", "name")
    assert_equal "_data/citations.yml", upload.dig("with", "path")
    assert_equal "error", upload.dig("with", "if-no-files-found")

    commit_steps = commit.fetch("steps")
    commit_checkout = commit_steps.find { |step| step.fetch("uses", "").start_with?("actions/checkout@") }
    download = commit_steps.find { |step| step.fetch("uses", "").start_with?("actions/download-artifact@") }
    publish = commit_steps.find { |step| step.fetch("run", "").include?("git push") }
    assert_equal true, commit_checkout.dig("with", "persist-credentials")
    assert_equal "${{ github.sha }}", commit_checkout.dig("with", "ref"),
                 "the artifact must be committed on the exact source revision that generated it"
    assert_equal "scholar-citations", download.dig("with", "name")
    assert_equal "${{ runner.temp }}/citations", download.dig("with", "path")
    assert_includes publish.fetch("run"),
                    'install -m 0644 "$RUNNER_TEMP/citations/citations.yml" _data/citations.yml'
    refute_includes publish.fetch("run"), "[skip deploy]",
                    "commit messages must not imply unsupported GitHub Actions skip semantics"
  end

  def test_manual_release_workflows_cannot_publish_from_nondefault_branches
    default_branch_guard = "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"

    citation_jobs = load_workflow(".github/workflows/update_scholar_citations.yml").fetch("jobs")
    %w[update commit].each do |job_name|
      assert_includes citation_jobs.fetch(job_name).fetch("if"), default_branch_guard,
                      "#{job_name} must not move a feature-branch artifact into the default branch"
    end

    deploy_jobs = load_workflow(".github/workflows/deploy.yml").fetch("jobs")
    build_guard = deploy_jobs.fetch("build").fetch("if")
    assert_includes build_guard, "github.event_name != 'workflow_dispatch'"
    assert_includes build_guard, default_branch_guard,
                    "manual Pages builds must not publish an unmerged feature branch"

    WORKFLOW_PATHS.each do |path|
      refute_includes read(path), "github.ref_name",
                      "#{path} must distinguish refs/heads/main from a same-named tag"
    end
  end

  def test_release_event_matrix_rejects_same_named_tags_and_unsafe_workflow_runs
    build_guard = load_workflow(".github/workflows/deploy.yml")
                  .fetch("jobs").fetch("build").fetch("if")
    assert_includes build_guard,
                    "github.event.workflow_run.conclusion == 'success'"
    assert_includes build_guard,
                    "github.event.workflow_run.head_branch == github.event.repository.default_branch"
    assert_includes build_guard,
                    "github.event.workflow_run.head_repository.full_name == github.repository"

    repository = "mtics/mtics.github.io"
    default_branch = "main"
    event_matrix = [
      {
        name: "manual default branch",
        event: "workflow_dispatch",
        ref: "refs/heads/main",
        expected: true
      },
      {
        name: "manual same-named tag",
        event: "workflow_dispatch",
        ref: "refs/tags/main",
        expected: false
      },
      {
        name: "manual feature branch",
        event: "workflow_dispatch",
        ref: "refs/heads/feature",
        expected: false
      },
      {
        name: "successful default-branch citation run",
        event: "workflow_run",
        conclusion: "success",
        head_branch: "main",
        head_repository: repository,
        expected: true
      },
      {
        name: "failed default-branch citation run",
        event: "workflow_run",
        conclusion: "failure",
        head_branch: "main",
        head_repository: repository,
        expected: false
      },
      {
        name: "successful nondefault citation run",
        event: "workflow_run",
        conclusion: "success",
        head_branch: "feature",
        head_repository: repository,
        expected: false
      },
      {
        name: "successful foreign-repository run",
        event: "workflow_run",
        conclusion: "success",
        head_branch: "main",
        head_repository: "attacker/fork",
        expected: false
      }
    ]

    event_matrix.each do |event|
      allowed = case event.fetch(:event)
                when "workflow_dispatch"
                  event.fetch(:ref) == "refs/heads/#{default_branch}"
                when "workflow_run"
                  event.fetch(:conclusion) == "success" &&
                    event.fetch(:head_branch) == default_branch &&
                    event.fetch(:head_repository) == repository
                end
      assert_equal event.fetch(:expected), allowed, event.fetch(:name)
    end
  end

  def test_citation_publish_uses_bounded_conflict_safe_push_retries
    jobs = load_workflow(".github/workflows/update_scholar_citations.yml").fetch("jobs")
    publish = jobs.fetch("commit").fetch("steps").find do |step|
      step.fetch("name", "").include?("Commit and push")
    end
    refute_nil publish
    script = publish.fetch("run")

    assert_match(/for attempt in 1 2 3/, script, "citation push retries must be explicitly bounded")
    assert_includes script, 'git fetch --no-tags origin "$default_branch"'
    assert_includes script, 'git rebase "origin/$default_branch"'
    assert_includes script, "git rebase --abort"
    assert_includes script, 'git push origin "HEAD:$default_branch"'
    assert_match(/if ! git rebase .*?; then.*?git rebase --abort.*?exit 1/ms, script,
                 "a citation-file conflict must abort the rebase and fail the job")
    refute_match(/git push[^\n]*(?:--force|-f)\b/, script)
    refute_match(/git pull/, script)
  end

  def test_citation_updater_contract_runs_before_the_secreted_refresh
    steps = load_workflow(".github/workflows/update_scholar_citations.yml")
            .fetch("jobs").fetch("update").fetch("steps")
    contract_index = steps.index do |step|
      step.fetch("run", "").include?("python test/citation_updater_contract_test.py")
    end
    update_index = steps.index do |step|
      step.fetch("run", "").include?("python bin/update_scholar_citations.py")
    end

    refute_nil contract_index, "the fail-closed citation updater contract must run in CI"
    refute_nil update_index
    assert_operator contract_index, :<, update_index
  end

  def test_citation_dependency_graphs_are_strictly_audited_before_secret_injection
    steps = load_workflow(".github/workflows/update_scholar_citations.yml")
            .fetch("jobs").fetch("update").fetch("steps")
    install_index = steps.index do |step|
      run = step.fetch("run", "")
      run.include?("python -m pip install") &&
        run.include?("--require-hashes") &&
        run.include?("-r requirements-build.txt") &&
        run.include?("-r requirements-citations.txt")
    end
    audit_index = steps.index do |step|
      run = step.fetch("run", "")
      run.include?("pip-audit --strict --requirement requirements-build.txt --no-deps --disable-pip") &&
        run.include?("pip-audit --strict --requirement requirements-citations.txt --no-deps --disable-pip")
    end
    contract_index = steps.index do |step|
      step.fetch("run", "").include?("python test/citation_updater_contract_test.py")
    end
    update_index = steps.index do |step|
      step.fetch("run", "").include?("python bin/update_scholar_citations.py")
    end

    refute_nil install_index, "citation automation must hash-install both reviewed dependency graphs"
    refute_nil audit_index, "citation automation must audit both graphs before exposing its secret"
    assert_operator install_index, :<, audit_index
    assert_operator audit_index, :<, contract_index
    assert_operator audit_index, :<, update_index
    refute_includes steps.fetch(audit_index).to_s, "SERPAPI_API_KEY"
    assert_includes steps.fetch(update_index).to_s, "SERPAPI_API_KEY"
  end

  def test_citation_refresh_chains_to_deploy_only_after_success
    deploy_workflow = load_workflow(".github/workflows/deploy.yml")
    citation_workflow = load_workflow(".github/workflows/update_scholar_citations.yml")
    triggers = deploy_workflow.fetch("on")

    assert triggers.key?("push"), "main pushes must continue to deploy"
    assert_includes Array(triggers.dig("push", "branches")), "main"
    refute triggers.key?("schedule"), "deploy must not race citation refresh on a clock"

    workflow_run = triggers.fetch("workflow_run")
    assert_includes Array(workflow_run.fetch("workflows")), citation_workflow.fetch("name")
    assert_includes Array(workflow_run.fetch("types")), "completed"

    build_condition = deploy_workflow.fetch("jobs").fetch("build").fetch("if")
    assert_includes build_condition, "github.event.workflow_run.conclusion == 'success'"
  end

  def test_legacy_deploy_script_is_non_destructive
    script = read("bin/deploy")
    assert_match(/bundle exec jekyll build/, script)

    refute_match(/git\s+push/, script)
    refute_match(/git\s+(?:checkout|switch|branch)/, script)
    refute_match(/\bpurgecss\b/, script)
    refute_match(/find\s+\.\s+.*-exec\s+rm/, script)
  end

  def test_deploy_builds_only_the_fixed_site_destination
    _stdout, _stderr, status, invocation = capture_deploy
    assert status.success?
    assert_equal %w[exec jekyll build --destination _site], invocation
  end

  def test_deploy_rejects_arguments_that_can_change_input_or_output_boundaries
    forbidden_arguments = [
      ["--destination", "/tmp/output"],
      ["--destination=/tmp/output"],
      ["-d", "/tmp/output"],
      ["-d=/tmp/output"],
      ["--source", "/tmp/source"],
      ["--source=/tmp/source"],
      ["-s", "/tmp/source"],
      ["-s=/tmp/source"],
      ["--config", "/tmp/config.yml"],
      ["--config=/tmp/config.yml"]
    ]

    forbidden_arguments.each do |arguments|
      _stdout, stderr, status, invocation = capture_deploy(*arguments)
      refute status.success?, "bin/deploy must reject #{arguments.join(' ')}"
      assert_empty invocation, "bundle must not run for #{arguments.join(' ')}"
      assert_match(/does not accept build options/i, stderr)
    end
  end

  def test_deploy_help_does_not_build
    stdout, _stderr, status, invocation = capture_deploy("--help")
    assert status.success?
    assert_empty invocation
    assert_match(/Usage: bin\/deploy/, stdout)
  end
end
