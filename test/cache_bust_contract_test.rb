# frozen_string_literal: true

require "digest/md5"
require "fileutils"
require "minitest/autorun"
require "nokogiri"
require "open3"
require "tmpdir"
require "yaml"

require "jekyll"
require "liquid"
require "jekyll-cache-bust"
require "al_folio_core"

PLUGIN_PATH = File.expand_path("../_plugins/local_asset_cache_bust.rb", __dir__)
PROJECT_ROOT = File.expand_path("..", __dir__)
require PLUGIN_PATH if File.file?(PLUGIN_PATH)
require File.expand_path("../_plugins/publication_security_filters.rb", __dir__)

class CacheBustContractTest < Minitest::Test
  def test_local_cache_bust_plugin_exists
    assert File.file?(PLUGIN_PATH), "expected a local cache-bust plugin at #{PLUGIN_PATH}"
    assert defined?(LocalAssetCacheBust), "expected LocalAssetCacheBust to be defined"
  end

  def test_unused_blog_and_book_generators_are_disabled
    assert_empty Dir.glob(File.join(PROJECT_ROOT, "_posts", "**", "*"), File::FNM_DOTMATCH).select { |path| File.file?(path) }
    assert_empty Dir.glob(File.join(PROJECT_ROOT, "_books", "**", "*"), File::FNM_DOTMATCH).select { |path| File.file?(path) }

    config = YAML.safe_load(File.read(File.join(PROJECT_ROOT, "_config.yml")), aliases: true)
    assert_equal false, config.dig("pagination", "enabled")
    assert_equal false, config.dig("related_blog_posts", "enabled")
    assert_equal [], config.dig("jekyll-archives", "posts", "enabled")
    assert_equal [], config.dig("jekyll-archives", "books", "enabled")
  end

  def test_css_fingerprint_includes_every_compilation_input_and_is_deterministic
    assert_respond_to LocalAssetCacheBust, :css_files_content

    Dir.mktmpdir("cache-bust-local-sass") do |source|
      Dir.mktmpdir("cache-bust-theme-sass") do |theme_root|
        FileUtils.mkdir_p(File.join(source, "_sass"))
        FileUtils.mkdir_p(File.join(source, "assets", "css"))
        FileUtils.mkdir_p(File.join(theme_root, "_sass"))
        FileUtils.mkdir_p(File.join(theme_root, "assets", "css"))

        inputs = {
          File.join(source, "_sass", "_local.scss") => "$local: #111;\n",
          File.join(source, "assets", "css", "main.scss") => "@use 'local';\n",
          File.join(theme_root, "_sass", "_theme.scss") => "$theme: #222;\n",
          File.join(theme_root, "assets", "css", "main.scss") => "@use 'theme';\n"
        }
        inputs.each { |path, content| File.write(path, content) }

        first = LocalAssetCacheBust.css_files_content(source: source, theme_root: theme_root)
        second = LocalAssetCacheBust.css_files_content(source: source, theme_root: theme_root)
        assert_equal Digest::MD5.hexdigest(first), Digest::MD5.hexdigest(second),
                     "unchanged CSS inputs must produce a stable fingerprint"

        inputs.each do |path, content|
          File.write(path, "#{content}/* changed */\n")
          changed = LocalAssetCacheBust.css_files_content(source: source, theme_root: theme_root)
          refute_equal Digest::MD5.hexdigest(first), Digest::MD5.hexdigest(changed),
                       "#{path} must affect the main.css fingerprint"
          File.write(path, content)
        end
      end
    end
  end

  def test_css_fingerprint_ignores_sass_and_entrypoint_symlinks_outside_each_real_root
    Dir.mktmpdir("cache-bust-css-confinement") do |workspace|
      source = File.join(workspace, "site")
      theme_root = File.join(workspace, "theme")
      outside = File.join(workspace, "outside")
      [source, theme_root].each do |root|
        FileUtils.mkdir_p(File.join(root, "_sass"))
        FileUtils.mkdir_p(File.join(root, "assets", "css"))
      end
      FileUtils.mkdir_p(outside)
      File.write(File.join(source, "_sass", "_safe.scss"), "$safe-local: #111;\n")
      File.write(File.join(theme_root, "_sass", "_safe.scss"), "$safe-theme: #222;\n")

      baseline = LocalAssetCacheBust.css_files_content(source: source, theme_root: theme_root)
      outside_sass = File.join(outside, "_secret.scss")
      outside_main = File.join(outside, "main.scss")
      File.write(outside_sass, "$outside-secret: #bad;\n")
      File.write(outside_main, "@use 'outside-secret';\n")
      File.symlink(outside_sass, File.join(source, "_sass", "_escape.scss"))
      File.symlink(outside_sass, File.join(theme_root, "_sass", "_escape.scss"))
      File.symlink(outside_main, File.join(source, "assets", "css", "main.scss"))
      File.symlink(outside_main, File.join(theme_root, "assets", "css", "main.scss"))
      File.symlink("_loop.scss", File.join(source, "_sass", "_loop.scss"))

      escaped = LocalAssetCacheBust.css_files_content(source: source, theme_root: theme_root)
      assert_equal baseline, escaped, "outside symlinks must not enter the CSS digest domain"
      refute_includes escaped, "$outside-secret"
      refute_includes escaped, "outside-secret"

      File.write(outside_sass, "$outside-secret: #def;\n")
      File.write(outside_main, "@use 'changed-outside-secret';\n")
      after_external_change = LocalAssetCacheBust.css_files_content(source: source, theme_root: theme_root)
      assert_equal Digest::MD5.hexdigest(baseline), Digest::MD5.hexdigest(after_external_change),
                   "changes beyond either real root must not alter the production CSS URL"
    end
  end

  def test_css_filter_is_isolated_by_liquid_site_and_render_order
    refute_respond_to LocalAssetCacheBust, :source
    refute_includes File.read(PLUGIN_PATH), "Jekyll::Hooks.register"

    Dir.mktmpdir("cache-bust-site-a") do |source_a|
      Dir.mktmpdir("cache-bust-site-b") do |source_b|
        write_css_input(source_a, "$site: #aaa;\n")
        write_css_input(source_b, "$site: #bbb;\n")
        site_a = jekyll_site(source_a)
        site_b = jekyll_site(source_b)

        first_a = render_liquid("{{ '/assets/css/main.css' | bust_css_cache }}", site_a)
        only_b = render_liquid("{{ '/assets/css/main.css' | bust_css_cache }}", site_b)
        second_a = render_liquid("{{ '/assets/css/main.css' | bust_css_cache }}", site_a)

        assert_equal first_a, second_a, "rendering another Site must not contaminate the first Site's digest"
        refute_equal first_a, only_b, "different Site sources must receive different CSS digests"
        assert_equal expected_css_url(source_a), first_a
        assert_equal expected_css_url(source_b), only_b
      end
    end
  end

  def test_pdf_url_fingerprint_tracks_file_content
    assert defined?(LocalAssetCacheBust), "LocalAssetCacheBust is required for local file URLs"

    Dir.mktmpdir("cache-bust-pdf") do |source|
      pdf_dir = File.join(source, "assets", "pdf")
      FileUtils.mkdir_p(pdf_dir)
      pdf = File.join(pdf_dir, "cv.pdf")
      File.binwrite(pdf, "%PDF-first")

      before = LocalAssetCacheBust.fingerprint_url("/assets/pdf/cv.pdf", source: source)
      File.binwrite(pdf, "%PDF-second")
      after = LocalAssetCacheBust.fingerprint_url("/assets/pdf/cv.pdf", source: source)

      assert_match %r{\A/assets/pdf/cv\.pdf\?v=[0-9a-f]{32}\z}, before
      refute_equal before, after, "CV PDF content changes must alter its public URL"
    end
  end

  def test_local_asset_filter_preserves_query_fragment_and_site_isolation
    Dir.mktmpdir("cache-bust-pdf-a") do |source_a|
      Dir.mktmpdir("cache-bust-pdf-b") do |source_b|
        pdf_a = write_pdf(source_a, "%PDF-site-a")
        pdf_b = write_pdf(source_b, "%PDF-site-b")
        site_a = jekyll_site(source_a)
        site_b = jekyll_site(source_b)
        template = "{{ '/assets/pdf/cv.pdf?download=1#page=2' | local_asset_fingerprint }}"

        first_a = render_liquid(template, site_a)
        only_b = render_liquid(template, site_b)
        second_a = render_liquid(template, site_a)

        expected_a = "/assets/pdf/cv.pdf?download=1&v=#{Digest::MD5.file(pdf_a).hexdigest}#page=2"
        expected_b = "/assets/pdf/cv.pdf?download=1&v=#{Digest::MD5.file(pdf_b).hexdigest}#page=2"
        assert_equal expected_a, first_a
        assert_equal expected_b, only_b
        assert_equal first_a, second_a, "filter results must remain isolated when Sites render in alternating order"
      end
    end
  end

  def test_fingerprint_url_rejects_parent_traversal_and_escaping_symlinks_without_hash_leaks
    Dir.mktmpdir("cache-bust-confinement") do |parent|
      source = File.join(parent, "site")
      FileUtils.mkdir_p(File.join(source, "assets", "pdf"))
      secret = File.join(parent, "secret.pdf")
      File.binwrite(secret, "%PDF-outside-secret")
      secret_digest = Digest::MD5.file(secret).hexdigest

      traversal = "/../secret.pdf"
      traversal_result = LocalAssetCacheBust.fingerprint_url(traversal, source: source)
      assert_equal traversal, traversal_result
      refute_includes traversal_result, secret_digest

      symlink_url = "/assets/pdf/escape.pdf"
      File.symlink(secret, File.join(source, "assets", "pdf", "escape.pdf"))
      symlink_result = LocalAssetCacheBust.fingerprint_url(symlink_url, source: source)
      assert_equal symlink_url, symlink_result
      refute_includes symlink_result, secret_digest
    end
  end

  def test_fingerprint_url_preserves_nonlocal_urls
    expectations = {
      "https://cdn.example/cv.pdf" => "https://cdn.example/cv.pdf",
      "//cdn.example/cv.pdf" => "https://cdn.example/cv.pdf",
      "mailto:person@example.com" => "mailto:person@example.com",
      "data:application/pdf;base64,AAAA" => "data:application/pdf;base64,AAAA",
      "#cv" => "#cv"
    }

    expectations.each do |value, expected|
      assert_equal expected, LocalAssetCacheBust.fingerprint_url(value, source: PROJECT_ROOT)
    end
  end

  def test_protocol_relative_urls_are_normalized_to_https
    assert_equal "https://cdn.example/cv.pdf?download=1#page=2",
                 LocalAssetCacheBust.fingerprint_url(
                   "//cdn.example/cv.pdf?download=1#page=2",
                   source: PROJECT_ROOT
                 )
  end

  def test_publication_attribute_filters_fail_closed_without_rewriting_valid_values
    filters = Object.new.extend(PublicationSecurityFilters)

    assert_equal "https://example.test/paper", filters.safe_http_url("https://example.test/paper")
    assert_equal "/publications/#paper", filters.safe_link_url("/publications/#paper")
    assert_equal "paper.pdf", filters.safe_local_asset_path("paper.pdf")
    assert_equal "2607.12345v2", filters.safe_publication_identifier("2607.12345v2")
    assert_equal "#b31b34", filters.safe_css_color("#b31b34")
    assert_equal "rebeccapurple", filters.safe_css_color("rebeccapurple")
    assert_equal "40rem", filters.safe_css_dimension("40rem")
    assert_equal "0", filters.safe_css_dimension("0")

    %w[javascript:alert(1) data:text/html,x vbscript:alert(1)].each do |unsafe|
      assert_equal "", filters.safe_http_url(unsafe)
      assert_equal "", filters.safe_link_url(unsafe)
    end
    assert_equal "", filters.safe_http_url(%(https://example.test/x" onerror="alert(1)))
    assert_equal "", filters.safe_local_asset_path("../outside.pdf")
    assert_equal "", filters.safe_publication_identifier(%(x" onmouseover="alert(1)))
    %w[#12 #12345 #1234567 #123456789 red;].each do |invalid_color|
      assert_equal "", filters.safe_css_color(invalid_color)
    end
    assert_equal "", filters.safe_css_dimension(%(1px;" onmouseover="alert(1)))
    assert_equal "", filters.safe_css_dimension("calc(100% - 1rem)")

    malicious_id = filters.safe_publication_dom_id(%(x" onmouseover="alert(1)))
    assert_match(/\A[A-Za-z][A-Za-z0-9_.:-]*\z/, malicious_id)
    assert_equal malicious_id, filters.safe_publication_dom_id(%(x" onmouseover="alert(1)))
  end

  def test_upstream_file_cache_bust_behavior_is_preserved
    Dir.mktmpdir("cache-bust-upstream") do |source|
      asset_dir = File.join(source, "assets", "js")
      FileUtils.mkdir_p(asset_dir)
      asset = File.join(asset_dir, "app.js")
      File.write(asset, "console.log('contract');\n")
      expected = "/assets/js/app.js?v=#{Digest::MD5.file(asset).hexdigest}"

      actual = Dir.chdir(source) do
        render_liquid("{{ '/assets/js/app.js' | bust_file_cache }}", jekyll_site(source))
      end
      assert_equal expected, actual
    end
  end

  def test_built_pages_share_the_content_fingerprinted_cv_url
    Dir.mktmpdir("cache-bust-site") do |destination|
      run_production_build(source: PROJECT_ROOT, destination: destination)

      expected_url = "/assets/pdf/cv.pdf?v=#{Digest::MD5.file(File.join(PROJECT_ROOT, "assets", "pdf", "cv.pdf")).hexdigest}"
      index = File.read(File.join(destination, "index.html"))
      cv = File.read(File.join(destination, "cv", "index.html"))

      assert_includes cv, %(href="#{expected_url}"), "CV header must fingerprint the PDF"
      assert_includes index, %(href="#{expected_url}"), "about-page social icon must fingerprint the PDF"
      assert_includes index, %(window.open("#{expected_url}", "_blank")), "search result must fingerprint the PDF"

      expected_main_css = expected_css_url(PROJECT_ROOT)
      assert_includes index, %(href="#{expected_main_css}"),
                      "production main.css must hash local/theme Sass and both main.scss entrypoints"
    end
  end

  def test_protocol_relative_cv_url_is_consistent_across_real_baseurl_build
    Dir.mktmpdir("cache-bust-protocol-build") do |workspace|
      source = File.join(workspace, "source")
      destination = File.join(workspace, "site")
      copy_project(source)
      protocol_relative_url = "//cdn.example/cv.pdf?download=1#page=2"
      replace_line(File.join(source, "_data", "socials.yml"), "cv_pdf:", "cv_pdf: #{protocol_relative_url}")
      replace_line(File.join(source, "_pages", "cv.md"), "cv_pdf:", "cv_pdf: #{protocol_relative_url}")

      run_production_build(source: source, destination: destination, baseurl: "/portfolio")

      expected = "https://cdn.example/cv.pdf?download=1#page=2"
      index = File.read(File.join(destination, "index.html"))
      cv = File.read(File.join(destination, "cv", "index.html"))
      cv_url = Nokogiri::HTML(cv).at_css('a[aria-label="Open CV PDF"]')&.[]("href")
      social_url = Nokogiri::HTML(index).at_css('a[title="Cv pdf"]')&.[]("href")
      search_url = index[/id: 'social-cv'.*?window\.open\("([^"]+)"/m, 1]

      assert_equal expected, cv_url
      assert_equal expected, social_url
      assert_equal expected, search_url
    end
  end

  def test_cv_template_uses_cache_bust_filter_for_local_pdf
    template = File.read(File.expand_path("../_includes/cv/render.liquid", __dir__))
    assert_includes template, "local_asset_fingerprint", "CV header links must use the site-confined local asset filter"
    refute_match(/page\.cv_pdf\s*\|[^\n]*bust_file_cache/, template,
                 "CV header must not send query/fragment-bearing URLs to the upstream file digester")
  end

  private

  def expected_css_url(source, file_name = "/assets/css/main.css")
    "#{file_name}?v=#{expected_css_digest(source: source, theme_root: AlFolioCore::THEME_ROOT)}"
  end

  def expected_css_digest(source:, theme_root:)
    records = [["local", source], ["theme", theme_root]].flat_map do |label, root|
      next [] if root.to_s.empty?

      paths = Dir.glob(File.join(root, "_sass", "**", "*")).select { |path| File.file?(path) }
      entrypoint = File.join(root, "assets", "css", "main.scss")
      paths << entrypoint if File.file?(entrypoint)
      paths.uniq.sort.map do |path|
        relative_path = path.delete_prefix("#{root}/")
        "#{label}/#{relative_path}\0#{File.binread(path)}\0"
      end
    end
    Digest::MD5.hexdigest(records.join)
  end

  def write_css_input(source, content)
    sass_dir = File.join(source, "_sass")
    FileUtils.mkdir_p(sass_dir)
    File.write(File.join(sass_dir, "_site.scss"), content)
  end

  def write_pdf(source, content)
    directory = File.join(source, "assets", "pdf")
    FileUtils.mkdir_p(directory)
    path = File.join(directory, "cv.pdf")
    File.binwrite(path, content)
    path
  end

  def jekyll_site(source)
    destination = File.join(source, "_site")
    Jekyll::Site.new(
      Jekyll.configuration(
        "source" => source,
        "destination" => destination,
        "disable_disk_cache" => true,
        "quiet" => true
      )
    )
  end

  def render_liquid(source, site)
    Liquid::Template.parse(source).render!({}, registers: { site: site })
  end

  def run_production_build(source:, destination:, baseurl: nil)
    env = {
      "JEKYLL_ENV" => "production",
      "SOURCE_DATE_EPOCH" => "1767225600"
    }
    command = [
      "bundle", "exec", "jekyll", "build",
      "--source", source,
      "--destination", destination,
      "--disable-disk-cache"
    ]
    command.concat(["--baseurl", baseurl]) if baseurl
    stdout, stderr, status = Open3.capture3(env, *command, chdir: PROJECT_ROOT)
    assert status.success?, "Jekyll build failed:\n#{stdout}\n#{stderr}"
    refute_match(/pagination.+enabled.+couldn't find any pagination page/i, "#{stdout}\n#{stderr}")
  end

  def copy_project(destination)
    FileUtils.mkdir_p(destination)
    excluded = %w[.git .jekyll-cache _site node_modules vendor]
    Dir.children(PROJECT_ROOT).each do |entry|
      next if excluded.include?(entry)

      FileUtils.cp_r(File.join(PROJECT_ROOT, entry), destination, preserve: true)
    end
  end

  def replace_line(path, prefix, replacement)
    content = File.read(path)
    replaced = content.sub(/^#{Regexp.escape(prefix)}.*$/, replacement)
    refute_equal content, replaced, "expected #{prefix.inspect} in #{path}"
    File.write(path, replaced)
  end
end
