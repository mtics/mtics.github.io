# frozen_string_literal: true

require "minitest/autorun"
require "liquid"
require "nokogiri"
require_relative "../_plugins/publication_security_filters"

class FixtureSocialLinksTag20260712 < Liquid::Tag
  def render(_context)
    '<a class="fixture-social" href="https://example.test/profile">Profile</a>'
  end
end

class FixtureNoopIncludeTag20260712 < Liquid::Tag
  def initialize(tag_name, markup, tokens)
    super
    @markup = markup
  end

  def render(context)
    return "" unless @markup.match?(/\A\s*figure\.liquid\b/)

    alt_reference = @markup[/\balt\s*=\s*([A-Za-z_]\w*)/, 1]
    figure_source = File.read(File.expand_path("../_includes/figure.liquid", __dir__))
    Liquid::Template.parse(figure_source).render!(
      {
        "include" => {
          "path" => "/fixture.png",
          "class" => "preview z-depth-1 rounded",
          "alt" => alt_reference ? context[alt_reference] : "",
          "loading" => "eager",
        },
        "site" => {
          "imagemagick" => { "enabled" => false },
          "lazy_loading_images" => false,
        },
      },
      filters: [FixtureUrlFilters20260712],
    )
  end
end

class FixtureFalseTag20260712 < Liquid::Tag
  def render(_context)
    "false"
  end
end

class FixtureEmptyTag20260712 < Liquid::Tag
  def render(_context)
    ""
  end
end

class FixtureHighlightBlock20260712 < Liquid::Block
  def render(context)
    super
  end
end

Liquid::Template.register_tag("social_links", FixtureSocialLinksTag20260712)
Liquid::Template.register_tag("include", FixtureNoopIncludeTag20260712)
Liquid::Template.register_tag("file_exists", FixtureFalseTag20260712)
Liquid::Template.register_tag("inspirehep_citations", FixtureEmptyTag20260712)
Liquid::Template.register_tag("highlight", FixtureHighlightBlock20260712)

module FixtureUrlFilters20260712
  def relative_url(input)
    input
  end

  def remove_accents(input)
    input
  end

  def regex_replace(input, pattern, replacement)
    input.to_s.gsub(Regexp.new(pattern), replacement)
  end

  def markdownify(input)
    input
  end

  def hideCustomBibtex(input)
    input
  end
end

class FrontendThemeAccessibilityContract20260712Test < Minitest::Test
  ROOT = File.expand_path("..", __dir__)
  SITE_DIR = ENV.fetch("SITE_DIR", File.join(ROOT, "_site"))

  def document(relative_path)
    Nokogiri::HTML5(File.read(File.join(SITE_DIR, relative_path)))
  end

  def render_header(pages)
    source = File.read(File.join(ROOT, "_includes/header.liquid"))
    html = Liquid::Template.parse(source).render!(
      {
        "page" => { "permalink" => "/fixture/", "title" => "Fixture", "url" => "/fixture/" },
        "site" => {
          "pages" => pages,
          "navbar_fixed" => true,
          "title" => "Fixture",
          "search_enabled" => false,
          "enable_darkmode" => false,
          "enable_progressbar" => false,
        },
      },
      filters: [FixtureUrlFilters20260712],
    )
    Nokogiri::HTML5.fragment(html)
  end

  def render_about(page)
    source = File.read(File.join(ROOT, "_layouts/about.liquid")).sub(/\A---.*?---\s*/m, "")
    html = Liquid::Template.parse(source).render!(
      {
        "page" => page,
        "site" => {
          "plugins" => [],
          "first_name" => "Fixture",
          "last_name" => "Person",
        },
        "content" => "Fixture content",
      },
      filters: [FixtureUrlFilters20260712],
    )
    Nokogiri::HTML5.fragment(html)
  end

  def render_bibliography_entry(entry, site_overrides = {})
    source = File.read(File.join(ROOT, "_layouts/bib.liquid")).sub(/\A---.*?---\s*/m, "")
    fixture_site = {
      "enable_publication_thumbnails" => false,
      "max_author_limit" => 1,
      "scholar" => { "last_name" => ["Fixture"], "first_name" => ["Person"] },
      "data" => { "coauthors" => {}, "venues" => {}, "citations" => { "papers" => {} }, "socials" => {} },
      "enable_publication_badges" => false,
      "enable_video_embedding" => false,
    }.merge(site_overrides)
    html = Liquid::Template.parse(source).render!(
      {
        "entry" => entry,
        "site" => fixture_site,
      },
      filters: [FixtureUrlFilters20260712, PublicationSecurityFilters],
    )
    Nokogiri::HTML5.fragment(html)
  end

  def render_figure(include_data)
    source = File.read(File.join(ROOT, "_includes/figure.liquid"))
    html = Liquid::Template.parse(source).render!(
      {
        "include" => include_data,
        "site" => {
          "imagemagick" => { "enabled" => false },
          "lazy_loading_images" => false,
        },
      },
      filters: [FixtureUrlFilters20260712, PublicationSecurityFilters],
    )
    Nokogiri::HTML5.fragment(html)
  end

  def test_back_to_top_is_a_named_native_button
    button = document("index.html").at_css("button#back-to-top")

    refute_nil button, "back-to-top must be present in the server-rendered HTML"
    assert_equal "button", button["type"]
    refute_empty button["aria-label"].to_s.strip
  end

  def test_every_built_page_parses_without_html5_tree_repair_errors
    pages = Dir.glob(File.join(SITE_DIR, "**", "*.html")).sort
    refute_empty pages, "build _site before validating HTML5"

    pages.each do |path|
      parsed = Nokogiri::HTML5(File.read(path), max_errors: 100)
      assert_empty parsed.errors.map(&:message),
                   "#{path.delete_prefix("#{SITE_DIR}/")} requires HTML5 tree repair"
    end
  end

  def test_page_title_does_not_create_a_second_banner
    page = document("news/index.html")

    assert_equal 1, page.css("header").length
    assert_nil page.at_css('[role="main"] header.post-header')
  end

  def test_navigation_landmarks_have_distinct_names
    names = document("cv/index.html").css("nav").map { |nav| nav["aria-label"].to_s.strip }

    refute_includes names, ""
    assert_includes names, "Primary navigation"
    assert_includes names, "Table of contents"
    assert_equal names.uniq, names
  end

  def test_each_dropdown_menu_is_labelled_by_its_own_unique_toggle
    dropdown = lambda do |title, order|
      {
        "title" => title,
        "permalink" => "/#{title.downcase}/",
        "url" => "/#{title.downcase}/",
        "nav" => true,
        "nav_order" => order,
        "dropdown" => true,
        "children" => [{ "title" => "Child", "permalink" => "/child-#{order}/" }],
      }
    end
    fragment = render_header([dropdown.call("Research", 1), dropdown.call("Resources", 2)])
    toggles = fragment.css("[data-nav-dropdown-toggle]")
    menus = fragment.css(".dropdown-menu")
    toggle_ids = toggles.map { |toggle| toggle["id"] }

    assert_equal 2, toggles.length
    assert_equal toggle_ids.uniq, toggle_ids
    assert_equal toggle_ids, menus.map { |menu| menu["aria-labelledby"] }
  end

  def test_navigation_permalinks_cannot_inject_attributes_or_unsafe_schemes
    payload = %(javascript://x" onmouseover="alert(1))
    pages = [
      {
        "title" => "Unsafe dropdown",
        "permalink" => "/unsafe-dropdown/",
        "url" => "/unsafe-dropdown/",
        "nav" => true,
        "nav_order" => 1,
        "dropdown" => true,
        "children" => [{ "title" => "Unsafe child", "permalink" => payload }],
      },
      { "title" => "Unsafe parent", "permalink" => payload, "url" => payload, "nav" => true, "nav_order" => 2 },
      { "title" => "Safe external", "permalink" => "https://example.test/page", "url" => "https://example.test/page", "nav" => true, "nav_order" => 3 },
      { "title" => "Safe local", "permalink" => "/safe-local/", "url" => "/safe-local/", "nav" => true, "nav_order" => 4 },
    ]
    fragment = render_header(pages)
    event_attributes = fragment.css("*").flat_map do |node|
      node.attribute_nodes.select { |attribute| attribute.name.downcase.start_with?("on") }
    end

    assert_empty event_attributes
    assert_empty fragment.css('[href^="javascript:"]')
    assert_nil fragment.css("a").find { |link| link.text.include?("Unsafe child") }
    assert_nil fragment.css("a").find { |link| link.text.include?("Unsafe parent") }
    assert fragment.at_css('a[href="https://example.test/page"]')
    assert fragment.at_css('a[href="/safe-local/"]')
  end

  def test_progress_element_has_valid_text_fallback
    progress = document("index.html").at_css("progress#progress")

    refute_nil progress
    assert_operator progress["max"].to_f, :>, 0
    assert_empty progress.css("div")
    assert_match(/\d+%/, progress.text)
  end

  def test_responsive_picture_markup_uses_valid_void_sources_and_dimensions
    html = File.read(File.join(SITE_DIR, "index.html"))
    page = Nokogiri::HTML5(html)

    refute_match(%r{</source>}i, html)
    page.css("picture source[srcset]").each do |source|
      refute_match(/,\s*\z/, source["srcset"])
    end
    page.css("picture img").each do |image|
      assert_match(/\A\d+\z/, image["width"]) if image["width"]
      assert_match(/\A\d+\z/, image["height"]) if image["height"]
    end
  end

  def test_figure_css_dimensions_cannot_escape_the_style_attribute
    payload = %(1px;" onmouseover="alert(1))
    fragment = render_figure(
      "path" => "/assets/img/fixture.png",
      "alt" => "Safe figure",
      "min-width" => payload,
      "min-height" => payload,
      "max-width" => "40rem",
      "max-height" => payload,
    )
    image = fragment.at_css("img")
    event_attributes = image.attribute_nodes.select do |attribute|
      attribute.name.downcase.start_with?("on") && attribute.name.downcase != "onerror"
    end

    assert_empty event_attributes
    assert_includes image["style"], "max-width: 40rem"
    refute_includes image["style"], "onmouseover"
    refute_includes image["style"], "min-width"
    refute_includes image["style"], "min-height"
    refute_includes image["style"], "max-height"
  end

  def test_local_publication_preview_alt_is_escaped_once
    title = "Privacy & Safety"
    fragment = render_bibliography_entry(
      {
        "key" => "preview-alt-fixture",
        "type" => "misc",
        "title" => title,
        "preview" => "fixture.png",
        "author_array" => [{ "first" => "Alice", "last" => "Safe" }],
      },
      { "enable_publication_thumbnails" => true },
    )
    image = fragment.at_css("img.preview")

    refute_nil image
    assert_equal title, image["alt"]
    refute_includes image["alt"], "&amp;"
  end

  def test_social_links_render_without_a_profile_block
    fragment = render_about("social" => true)

    refute_nil fragment.at_css(".profile-social")
    refute_nil fragment.at_css(".profile-social .fixture-social")
    assert_includes fragment.text, "Fixture content"
  end

  def test_quoted_author_names_are_text_not_executable_disclosure_code
    malicious_name = %(Eve" onmouseover="alert(1) O'Connor<script>alert(2)</script>)
    fragment = render_bibliography_entry(
      "key" => "quoted-author-fixture",
      "type" => "misc",
      "title" => "Quoted author fixture",
      "author_array" => [
        { "first" => "Alice", "last" => "Safe" },
        { "first" => %(Eve" onmouseover="alert(1)), "last" => %(O'Connor<script>alert(2)</script>) },
      ],
    )
    author = fragment.at_css(".author")
    disclosure = author&.at_css("button.more-authors")
    event_attributes = author&.xpath(".//*").to_a.flat_map { |node| node.attribute_nodes.map(&:name) }.grep(/^on/i)

    refute_nil disclosure
    assert_empty event_attributes
    assert_nil disclosure["onclick"]
    assert_equal "false", disclosure["aria-expanded"]
    assert disclosure.key?("data-more-authors-toggle")
    assert_includes author.text, malicious_name
    assert_empty author.css("script")
  end

  def test_publication_annotation_uses_native_disclosure
    fragment = render_bibliography_entry(
      "key" => "annotation-fixture",
      "type" => "misc",
      "title" => "Annotation fixture",
      "author_array" => [{ "first" => "Alice", "last" => "Safe" }],
      "annotation" => %(Quoted "note" <script>alert(1)</script>),
    )
    details = fragment.at_css("details.publication-annotation")

    refute_nil details
    refute_nil details.at_css("summary")
    assert_includes details.text, %(Quoted "note" <script>alert(1)</script>)
    assert_empty details.css("script")
    assert_empty fragment.css('[data-toggle="popover"]')
  end

  def test_arxiv_links_use_transport_security
    fragment = render_bibliography_entry(
      "key" => "arxiv-fixture",
      "type" => "misc",
      "title" => "arXiv fixture",
      "arxiv" => "2607.12345",
      "author_array" => [{ "first" => "Alice", "last" => "Safe" }],
    )

    assert_equal "https://arxiv.org/abs/2607.12345", fragment.at_css('a[href*="arxiv.org"]')&.[]("href")
  end

  def test_publication_navigation_links_keep_native_link_semantics
    fragment = render_bibliography_entry(
      "key" => "link-semantics-fixture",
      "type" => "misc",
      "title" => "Link semantics fixture",
      "doi" => "10.0000/fixture",
      "arxiv" => "2607.12345",
      "website" => "https://example.test/paper",
      "abstract" => "Fixture abstract",
      "author_array" => [{ "first" => "Alice", "last" => "Safe" }],
    )

    navigation_links = fragment.css('.links a:not([data-bib-disclosure])')
    refute_empty navigation_links
    navigation_links.each { |link| assert_nil link["role"], "#{link.text.strip} must remain a native link" }
    assert_equal "button", fragment.at_css('a[data-bib-disclosure]')&.[]("role")
  end

  def test_publication_attribute_inputs_cannot_escape_or_create_unsafe_urls
    payload = %(x" onmouseover="alert(1))
    fragment = render_bibliography_entry(
      {
        "key" => payload,
        "type" => "misc",
        "title" => "Adversarial publication fixture",
        "abbr" => "Fixture",
        "preview" => %(https://images.example.test/x" onerror="alert(1)),
        "doi" => %(10.1000/x" onmouseover="alert(1)),
        "arxiv" => payload,
        "hal" => payload,
        "html" => "javascript:alert(html)",
        "pdf" => payload,
        "supp" => payload,
        "video" => "javascript:alert(video)",
        "blog" => "javascript:alert(blog)",
        "code" => "javascript:alert(code)",
        "poster" => payload,
        "slides" => payload,
        "website" => "javascript:alert(website)",
        "google_scholar_id" => payload,
        "altmetric" => payload,
        "dimensions" => payload,
        "eprint" => payload,
        "pmid" => payload,
        "isbn" => payload,
        "inspirehep_id" => payload,
        "abstract" => "Safe abstract",
        "award" => "Safe award",
        "bibtex_show" => true,
        "author_array" => [{ "first" => "Alice", "last" => "Safe" }],
      },
      {
        "enable_publication_thumbnails" => true,
        "enable_publication_badges" => {
          "google_scholar" => true,
          "altmetric" => true,
          "dimensions" => true,
          "inspirehep" => true,
        },
        "data" => {
          "coauthors" => {
            "safe" => [{ "firstname" => "Alice", "url" => "javascript:alert(coauthor)" }],
          },
          "venues" => {
            "Fixture" => {
              "url" => "javascript:alert(venue)",
              "color" => %(red;" onmouseover="alert(1)),
            },
          },
          "citations" => { "papers" => {} },
          "socials" => { "scholar_userid" => payload },
        },
      },
    )

    event_attributes = fragment.css("*").flat_map do |node|
      node.attribute_nodes.select { |attribute| attribute.name.downcase.start_with?("on") }
    end
    unsafe_urls = fragment.css("[href], [src]").filter_map do |node|
      %w[href src].filter_map do |attribute|
        value = node[attribute]
        "#{attribute}=#{value}" if value&.match?(/\A(?:javascript|data|vbscript):/i)
      end
    end.flatten
    ids = fragment.css("[id]").map { |node| node["id"] }

    assert_empty event_attributes
    assert_empty unsafe_urls
    assert_empty fragment.css(".author a"), "an unsafe coauthor URL must fall back to plain text"
    assert ids.all? { |id| id.match?(/\A[A-Za-z][A-Za-z0-9_.:-]*\z/) }, ids.inspect
    fragment.css("[aria-controls]").each do |control|
      assert_includes ids, control["aria-controls"]
    end
  end

  def test_scholar_citation_fallback_matches_the_publication_id_exactly
    publication_id = "Target42"
    fragment = render_bibliography_entry(
      {
        "key" => "citation-id-fixture",
        "type" => "misc",
        "title" => "Citation ID fixture",
        "google_scholar_id" => publication_id,
        "author_array" => [{ "first" => "Alice", "last" => "Safe" }],
      },
      {
        "enable_publication_badges" => { "google_scholar" => true },
        "data" => {
          "coauthors" => {},
          "venues" => {},
          "socials" => { "scholar_userid" => "current-user" },
          "citations" => {
            "papers" => {
              "unrelated-#{publication_id}-suffix" => { "citations" => 99 },
              "legacy-user:#{publication_id}" => { "citations" => 7 },
            },
          },
        },
      },
    )

    assert_equal "7", fragment.at_css(".scholar-citation-count")&.text
  end

  def test_publication_panel_disclosures_do_not_use_inline_javascript
    controls = document("publications/index.html").css(".publications .links [aria-controls]")

    refute_empty controls
    controls.each do |control|
      assert control.key?("data-bib-disclosure")
      assert_nil control["onclick"]
      assert_nil control["onkeydown"]
    end
  end
end
