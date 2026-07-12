# frozen_string_literal: true

require "date"
require "digest/md5"
require "fileutils"
require "json"
require "minitest/autorun"
require "nokogiri"
require "tmpdir"
require "yaml"
require "jekyll"

class CvSchemaRenderingContractTest < Minitest::Test
  ROOT = File.expand_path("..", __dir__)
  FIXTURES = File.join(__dir__, "fixtures")
  TEMPLATE = File.join(ROOT, "_includes", "cv", "render.liquid")

  def setup
    @destination = Dir.mktmpdir("cv-schema-rendering-contract")
    config = Jekyll.configuration(
      "source" => ROOT,
      "destination" => @destination,
      "disable_disk_cache" => true,
      "quiet" => true
    )
    @site = Jekyll::Site.new(config)
    @site.reset
    @site.read
  end

  def teardown
    FileUtils.remove_entry(@destination) if File.directory?(@destination)
  end

  def test_rendercv_routes_lowercase_education_by_entry_shape
    document = render_fixture("cv_rendercv_v28.yml", format: "rendercv")

    assert_includes section_text(document, "education"), "Example University"
    assert_includes section_text(document, "education"), "PhD"
  end

  def test_rendercv_routes_arbitrary_experience_title_by_entry_shape
    document = render_fixture("cv_rendercv_v28.yml", format: "rendercv")

    assert_includes section_text(document, "Employment History"), "Example Labs"
    assert_includes section_text(document, "Employment History"), "Research Engineer"
  end

  def test_rendercv_renders_normal_entry_in_arbitrary_section
    document = render_fixture("cv_rendercv_v28.yml", format: "rendercv")

    selected_work = section_text(document, "Selected Work")
    assert_includes selected_work, "Analytical Engine"
    assert_includes selected_work, "A normal RenderCV entry under an arbitrary section title."
    assert_includes selected_work, "Preserved the authored entry instead of dropping it."
  end

  def test_rendercv_normal_entry_with_arbitrary_shape_like_metadata_stays_normal
    document = render_fixture("cv_rendercv_v28.yml", format: "rendercv")

    entry_heading = heading(document, "Analytical Engine")
    refute_nil entry_heading, "a schema-valid NormalEntry must keep its name when arbitrary metadata resembles another entry shape"
    assert_equal "h3", entry_heading.name
  end

  def test_rendercv_bullet_entry_wins_over_arbitrary_number_metadata
    document = render_fixture("cv_rendercv_v28.yml", format: "rendercv")

    notes = section_text(document, "Notes")
    assert_includes notes, "Visible bullet content survives arbitrary metadata."
  end

  def test_rendercv_v28_header_uses_headline_and_flat_location
    document = render_fixture("cv_rendercv_v28.yml", format: "rendercv")
    contact = section_text(document, "Contact Information")

    assert_includes contact, "Research Engineer"
    assert_includes contact, "Sydney, Australia"
  end

  def test_rendercv_v28_header_preserves_scalar_and_array_contact_values
    array_document = render_fixture("cv_rendercv_v28.yml", format: "rendercv")
    scalar_document = render_fixture("cv_rendercv_v28_scalar.yml", format: "rendercv")

    %w[ada@rendercv.com ada.work@rendercv.com +61\ 2\ 0000\ 0000 +44\ 20\ 0000\ 0000].each do |value|
      assert_includes normalized_text(array_document), value
    end
    assert_equal ["https://ada.example.test", "https://work.example.test/ada"],
                 array_document.css("a").filter_map { |link| link["href"] if link["href"]&.start_with?("https://") && link.text.include?("example.test") }

    %w[grace@rendercv.com +14155551234].each do |value|
      assert_includes normalized_text(scalar_document), value
    end
    assert scalar_document.at_css('a[href="https://grace.example.test"]')

    assert_equal %w[mailto:ada@rendercv.com mailto:ada.work@rendercv.com],
                 array_document.css('a[href^="mailto:"]').map { |link| link["href"] }
    assert_equal %w[tel:+61200000000 tel:+442000000000],
                 array_document.css('a[href^="tel:"]').map { |link| link["href"] }
    assert scalar_document.at_css('a[href="mailto:grace@rendercv.com"]')
    assert scalar_document.at_css('a[href="tel:+14155551234"]')
  end

  def test_cv_renderers_do_not_turn_unsafe_urls_into_links
    rendercv_document = render_cv_data(
      {
        "cv" => {
          "name" => "Unsafe URL Example",
          "website" => ["javascript:alert(website)"],
          "sections" => {
            "Selected Work" => [
              {
                "name" => "Unsafe normal entry",
                "url" => "javascript:alert(normal)",
                "summary" => "The authored text remains visible.",
              },
            ],
          },
        },
      },
      format: "rendercv",
      page: { "cv_pdf" => "javascript:alert(pdf)" }
    )
    jsonresume_document = render_cv_data(
      {
        "basics" => { "name" => "Unsafe JSON Resume Example" },
        "awards" => [
          {
            "title" => "Unsafe award link",
            "url" => "javascript:alert(award)",
          },
        ],
      },
      format: "jsonresume"
    )

    assert_nil rendercv_document.at_css('a[aria-label="Open CV PDF"]')
    assert_empty rendercv_document.css('a[href^="javascript:"]')
    assert_includes normalized_text(rendercv_document), "Unsafe normal entry"
    assert_includes normalized_text(rendercv_document), "javascript:alert(website)"
    assert_empty jsonresume_document.css('a[href^="javascript:"]')
    assert_includes normalized_text(jsonresume_document), "Unsafe award link"
  end

  def test_cv_icon_extensions_cannot_inject_event_handler_attributes
    payload = %(fa" onmouseover="alert(1))
    documents = {
      "OneLineEntry" => render_cv_partial(
        "one_line.liquid",
        "entries" => [{ "label" => "Safe label", "details" => "Safe details", "icon" => payload }]
      ),
      "language" => render_cv_partial(
        "languages.liquid",
        "entries" => [{ "language" => "English", "fluency" => "Fluent", "icon" => payload }]
      ),
    }

    documents.each do |label, document|
      injected_attributes = document.css("*").flat_map do |node|
        node.attribute_nodes.select { |attribute| attribute.name.downcase.start_with?("on") }
      end
      assert_empty injected_attributes, "#{label} emitted an event-handler attribute"
    end
  end

  def test_publication_doi_accepts_an_identifier_or_canonical_url_without_attribute_injection
    document = render_cv_partial(
      "publications.liquid",
      "entries" => [
        { "title" => "Identifier DOI", "doi" => "10.1000/identifier" },
        { "title" => "Canonical DOI", "doi" => "https://doi.org/10.1000/canonical" },
        { "title" => "Legacy DOI resolver", "doi" => "https://dx.doi.org/10.1000/legacy" },
        { "title" => "Prefixed DOI", "doi" => "doi:10.1000/prefixed" },
        { "title" => "Repeated DOI prefixes", "doi" => "doi:https://doi.org/doi:10.1000/repeated" },
        { "title" => "Invalid DOI", "doi" => "not-a-doi" },
        { "title" => "Quoted DOI", "doi" => %(10.1000/quoted\" onmouseover=\"alert(1)) },
      ]
    )

    assert document.at_css('a[href="https://doi.org/10.1000/identifier"]')
    assert document.at_css('a[href="https://doi.org/10.1000/canonical"]')
    assert document.at_css('a[href="https://doi.org/10.1000/legacy"]')
    assert document.at_css('a[href="https://doi.org/10.1000/prefixed"]')
    assert document.at_css('a[href="https://doi.org/10.1000/repeated"]')
    assert_nil heading(document, "Invalid DOI").at_css("a")
    assert_nil heading(document, "Quoted DOI").at_css("a")
    assert_includes normalized_text(document), "doi:not-a-doi"
    assert_includes normalized_text(document), %(doi:10.1000/quoted\" onmouseover=\"alert(1))
  end

  def test_multiblock_markdown_remains_valid_html_in_every_cv_entry_family
    rich_text = "First paragraph.\n\n1. Nested ordered item"
    rendercv_html = render_cv_html(
      {
        "cv" => {
          "name" => "Rich Markdown Example",
          "summary" => rich_text,
          "sections" => {
            "Education" => [
              {
                "institution" => "Example University",
                "area" => "Research",
                "summary" => rich_text,
                "highlights" => [rich_text],
              },
            ],
            "Experience" => [
              {
                "company" => "Example Labs",
                "position" => "Engineer",
                "description" => rich_text,
                "summary" => rich_text,
                "highlights" => [rich_text],
              },
            ],
            "Normal" => [{ "name" => "Normal Entry", "summary" => rich_text, "highlights" => [rich_text] }],
            "Publications" => [{ "title" => "Publication Entry", "authors" => ["Ada"], "summary" => rich_text }],
            "One Line" => [{ "label" => "Label", "details" => rich_text }],
            "Bullets" => [{ "bullet" => rich_text }],
          },
        },
      },
      format: "rendercv"
    )
    jsonresume_html = render_cv_html(
      {
        "basics" => { "name" => "Rich JSON Resume", "summary" => rich_text },
        "awards" => [{ "title" => "Award Entry", "summary" => rich_text }],
        "projects" => [{ "name" => "Project Entry", "description" => rich_text, "highlights" => [rich_text] }],
      },
      format: "jsonresume"
    )

    { "RenderCV" => rendercv_html, "JSON Resume" => jsonresume_html }.each do |label, html|
      fragment = Nokogiri::HTML5.fragment(html, max_errors: 100)
      assert_empty fragment.errors.map(&:message), "#{label} emitted invalid HTML5"
      assert_operator fragment.css("ol").size, :>, 0, "#{label} dropped nested Markdown lists"
    end
  end

  def test_rendercv_numbered_and_reversed_numbered_lists_keep_their_direction
    document = render_fixture("cv_rendercv_v28_edges.yml", format: "rendercv")
    numbered_list = heading(document, "Numbered Work").parent.at_css("ol")
    reversed_list = heading(document, "Reverse Chronology").parent.at_css("ol")

    refute_nil numbered_list
    refute numbered_list.key?("reversed"), "NumberedEntry must count upward"
    assert_equal ["First numbered item", "Second numbered item"],
                 numbered_list.css("li").map { |item| normalized_text(item) }

    refute_nil reversed_list
    assert reversed_list.key?("reversed"), "ReversedNumberedEntry must emit an HTML reversed list"
    assert_equal ["Latest reverse-numbered item", "Earlier reverse-numbered item"],
                 reversed_list.css("li").map { |item| normalized_text(item) }
  end

  def test_rendercv_all_v28_social_networks_link_to_their_canonical_profile_urls
    document = render_fixture("cv_rendercv_v28_edges.yml", format: "rendercv")
    expected_urls = {
      "LinkedIn" => "https://linkedin.com/in/ada-lovelace",
      "GitHub" => "https://github.com/ada-lovelace",
      "GitLab" => "https://gitlab.com/ada-lovelace",
      "IMDB" => "https://imdb.com/name/nm0000123",
      "Instagram" => "https://instagram.com/ada-lovelace",
      "ORCID" => "https://orcid.org/0000-0002-1825-0097",
      "Mastodon" => "https://mastodon.social/@ada",
      "StackOverflow" => "https://stackoverflow.com/users/12345/ada-lovelace",
      "ResearchGate" => "https://researchgate.net/profile/Ada_Lovelace",
      "YouTube" => "https://youtube.com/@ada-lovelace",
      "Google Scholar" => "https://scholar.google.com/citations?user=scholar-token",
      "Telegram" => "https://t.me/ada_lovelace",
      "WhatsApp" => "https://wa.me/+61412345678",
      "Leetcode" => "https://leetcode.com/u/ada-lovelace",
      "X" => "https://x.com/ada_lovelace",
      "Bluesky" => "https://bsky.app/profile/ada.bsky.social",
      "Reddit" => "https://reddit.com/user/ada_lovelace",
    }

    expected_urls.each do |network, expected_url|
      row = document.css("tr").find { |candidate| normalized_text(candidate.at_css("b")) == network }
      refute_nil row, "expected a contact row for #{network}"
      assert_equal expected_url, row.at_css("a")&.[]("href"), "incorrect #{network} profile URL"
    end
  end

  def test_social_profile_explicit_url_wins_and_unknown_networks_degrade_to_text
    document = render_cv_partial(
      "social_networks.liquid",
      "social_networks" => [
        {
          "network" => "GitHub",
          "username" => "derived-url-must-not-win",
          "url" => "https://profiles.example.test/explicit",
        },
        { "network" => "Fediverse Next", "username" => "safe-user" },
      ]
    )

    assert document.at_css('a[href="https://profiles.example.test/explicit"]')
    unknown_row = document.css("tr").find { |candidate| normalized_text(candidate.at_css("b")) == "Fediverse Next" }
    refute_nil unknown_row
    assert_includes normalized_text(unknown_row), "safe-user"
    assert_empty unknown_row.css("a"), "unknown networks must not guess a potentially broken profile URL"
  end

  def test_rendercv_custom_connections_keep_placeholder_url_and_icon
    document = render_fixture("cv_rendercv_v28_edges.yml", format: "rendercv")
    linked_row = document.css("tr").find { |candidate| normalized_text(candidate).include?("Book a call") }
    unlinked_row = document.css("tr").find { |candidate| normalized_text(candidate).include?("Academic office") }

    refute_nil linked_row
    assert linked_row.at_css('i.fa-calendar-days[aria-hidden="true"]')
    assert_equal "https://calendar.example.test/ada", linked_row.at_css("a")&.[]("href")

    refute_nil unlinked_row
    assert unlinked_row.at_css('i.fa-building-columns[aria-hidden="true"]')
    assert_empty unlinked_row.css("a")
  end

  def test_rendercv_local_and_remote_photos_are_visible_and_accessible
    local_document = render_fixture("cv_rendercv_v28_edges.yml", format: "rendercv")
    remote_document = render_cv_data(
      {
        "cv" => {
          "name" => "Grace Hopper",
          "photo" => "https://images.example.test/grace-hopper.jpg",
          "sections" => {},
        },
      },
      format: "rendercv"
    )
    local_photo = local_document.at_css("img.cv-photo")
    remote_photo = remote_document.at_css("img.cv-photo")

    refute_nil local_photo
    assert_equal "Ada Lovelace portrait", local_photo["alt"]
    assert_match(%r{\A/assets/img/prof_pic\.jpg\?v=[0-9a-f]{32}\z}, local_photo["src"])

    refute_nil remote_photo
    assert_equal "Grace Hopper portrait", remote_photo["alt"]
    assert_equal "https://images.example.test/grace-hopper.jpg", remote_photo["src"]
  end

  def test_unsafe_compatibility_photo_url_is_not_rendered
    document = render_cv_data(
      { "cv" => { "name" => "Unsafe Example", "photo" => "javascript:alert(1)", "sections" => {} } },
      format: "rendercv"
    )

    assert_empty document.css("img.cv-photo")
  end

  def test_nonpublic_local_photo_uses_an_accessible_placeholder_without_leaking_its_path
    document = render_cv_data(
      { "cv" => { "name" => "Local Example", "photo" => "private/portrait.jpg", "sections" => {} } },
      format: "rendercv"
    )
    placeholder = document.at_css('.cv-photo-placeholder[role="img"]')

    refute_nil placeholder
    assert_equal "Local Example portrait unavailable", placeholder["aria-label"]
    refute_includes document.to_html, "private/portrait.jpg"
  end

  def test_rendercv_end_date_without_start_date_is_a_single_date
    document = render_fixture("cv_rendercv_v28_edges.yml", format: "rendercv")
    expected_dates = {
      "End-only Experience" => "2024-05",
      "End-only Education" => "2023-06",
      "End-only Normal" => "2022",
    }

    expected_dates.each do |section_title, expected_date|
      badges = heading(document, section_title).parent.css(".badge")
      assert_equal [expected_date], badges.map { |badge| normalized_text(badge) }
      refute_includes normalized_text(heading(document, section_title).parent), "#{expected_date} -"
    end
  end

  def test_rendercv_publication_title_prefers_doi_over_url
    document = render_fixture("cv_rendercv_v28_edges.yml", format: "rendercv")
    publication_heading = heading(document, "Canonical DOI Publication")

    refute_nil publication_heading
    assert_equal "https://doi.org/10.1000/rendercv-edge", publication_heading.at_css("a")&.[]("href")
    refute document.at_css('a[href="https://publisher.example.test/noncanonical-copy"]')
  end

  def test_current_cv_uses_rendercv_v28_field_shapes
    cv = load_yaml(File.join(ROOT, "_data", "cv.yml")).fetch("cv")

    assert_equal "PhD Candidate", cv["headline"]
    assert_equal "Sydney, Australia", cv["location"]
    refute cv.key?("label")
    refute cv.key?("address")
    refute cv.key?("image")
    refute cv.key?("summary")

    education = cv.fetch("sections").fetch("Education")
    assert education.all? { |entry| entry.key?("degree") && !entry.key?("studyType") }
    awards = cv.fetch("sections").fetch("Awards")
    assert awards.all? { |entry| entry.key?("name") && !entry.key?("title") }
    %w[Service Languages].each do |section|
      assert cv.fetch("sections").fetch(section).all? { |entry| entry.key?("label") && entry.key?("details") }
    end
  end

  def test_missing_dates_and_locations_do_not_emit_empty_metadata
    document = render_fixture("cv_rendercv_empty_fields.yml", format: "rendercv")

    assert_empty document.css(".cv .badge")
    assert_empty document.css(".cv .location")
  end

  def test_empty_field_fixture_keeps_the_v28_education_required_shape
    cv = load_yaml(File.join(FIXTURES, "cv_rendercv_empty_fields.yml")).fetch("cv")
    education = cv.fetch("sections").fetch("Education").first

    assert education.key?("institution")
    assert education.key?("area"), "RenderCV v2.8 EducationEntry requires area even when date and location are absent"
    refute education.key?("date")
    refute education.key?("location")
  end

  def test_blank_award_date_does_not_emit_metadata_nodes
    document = render_cv_partial(
      "awards.liquid",
      "entries" => [{ "title" => "Undated Award", "date" => " \t " }]
    )
    award = document.at_css("li.list-group-item")

    refute_nil award
    assert_includes normalized_text(award), "Undated Award"
    assert_empty award.css(".cv-entry-meta")
    assert_empty award.css(".badge")
    assert_equal 1, award.css(".cv-entry-content").size,
                 "undated awards must retain the shared typography and spacing contract"
  end

  def test_rendercv_section_ids_are_collision_safe
    document = render_fixture("cv_rendercv_colliding_sections.yml", format: "rendercv")
    ids = document.css(".cv h2[id]").map { |heading| heading["id"] }

    assert_equal ids.uniq, ids
    assert_equal "c-2", heading(document, "C-2")["id"], "an ordinary section slug should keep its stable deep link"
    assert_equal "c", heading(document, "C++")["id"]
    assert_equal "c-3", heading(document, "C#")["id"]
  end

  def test_jsonresume_renders_basics_profiles
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")
    contact = section_text(document, "Contact Information")

    assert_includes contact, "GitHub"
    assert_includes contact, "ada-lovelace"
    assert document.at_css('a[href="https://profiles.example.test/ada"]'),
           "an explicit JSON Resume profile URL must win over the network-derived fallback"
  end

  def test_jsonresume_fixture_covers_every_official_v1_3_visual_field
    resume = JSON.parse(File.read(File.join(FIXTURES, "cv_jsonresume_contract.json")))
    expected_fields = {
      "basics" => %w[name label image email phone url summary location profiles],
      "work" => %w[name location description position url startDate endDate summary highlights],
      "volunteer" => %w[organization position url startDate endDate summary highlights],
      "education" => %w[institution url area studyType startDate endDate score courses],
      "awards" => %w[title date awarder summary],
      "certificates" => %w[name date url issuer],
      "publications" => %w[name publisher releaseDate url summary],
      "skills" => %w[name level keywords],
      "languages" => %w[language fluency],
      "interests" => %w[name keywords],
      "references" => %w[name reference],
      "projects" => %w[name description highlights keywords startDate endDate url roles entity type],
    }

    expected_fields.each do |section, fields|
      entries = section == "basics" ? [resume.fetch(section)] : resume.fetch(section)
      covered_fields = entries.flat_map(&:keys).uniq
      assert_empty fields - covered_fields, "#{section} fixture is missing official fields"
    end
    location = resume.fetch("basics").fetch("location")
    assert_empty %w[address postalCode city countryCode region] - location.keys
    profile = resume.fetch("basics").fetch("profiles").first
    assert_empty %w[network username url] - profile.keys
  end

  def test_full_jsonresume_fixture_survives_real_jekyll_render_and_write
    @site.data["cv"] = nil
    @site.data["resume"] = JSON.parse(File.read(File.join(FIXTURES, "cv_jsonresume_contract.json")))
    cv_page = @site.pages.find { |candidate| candidate.url == "/cv/" }
    refute_nil cv_page
    cv_page.data["cv_format"] = "jsonresume"

    @site.render
    @site.cleanup
    @site.write

    output = File.join(@destination, "cv", "index.html")
    assert File.file?(output), "real Jekyll write must emit the JSON Resume CV page"
    document = Nokogiri::HTML(File.read(output))
    expected_sections = %w[Experience Education Awards Publications Skills Languages Interests Certificates Projects References]
    expected_sections.each { |title| refute_nil heading(document, title), "missing #{title} after a real Jekyll build" }
    assert_equal "Ada Lovelace portrait", document.at_css("img.cv-photo")&.[]("alt")
    assert_includes normalized_text(document), "Open Standards Lab"
  end

  def test_jsonresume_fixture_is_natively_loadable_from_data_resume_json
    Dir.mktmpdir("jsonresume-native-data") do |source|
      FileUtils.mkdir_p(File.join(source, "_data"))
      FileUtils.cp(
        File.join(FIXTURES, "cv_jsonresume_contract.json"),
        File.join(source, "_data", "resume.json")
      )
      site = Jekyll::Site.new(
        Jekyll.configuration(
          "source" => source,
          "destination" => File.join(source, "_site"),
          "disable_disk_cache" => true,
          "safe" => true,
          "third_party_libraries" => { "download" => false },
          "quiet" => true
        )
      )

      site.reset
      site.read
      assert_equal "Ada Lovelace", site.data.dig("resume", "basics", "name")
      assert_equal "Research Engineer", site.data.dig("resume", "work", 0, "position")
    end
  end

  def test_jsonresume_data_contract_rejects_ghost_entry_shapes
    invalid_documents = {
      "scalar root" => "not-an-object",
      "scalar section" => { "work" => "not-an-array" },
      "object section" => { "education" => { "institution" => "Ghost University" } },
      "scalar entry" => { "projects" => ["not-an-object"] },
      "scalar profiles" => { "basics" => { "profiles" => "not-an-array" } },
      "non-object profile" => { "basics" => { "profiles" => ["not-an-object"] } },
      "scalar highlights" => { "work" => [{ "name" => "Ghost", "highlights" => "not-an-array" }] },
      "non-string keyword" => { "skills" => [{ "name" => "Ghost", "keywords" => [{ "bad" => true }] }] },
      "unknown-only entry" => { "awards" => [{ "extension" => "not rendered" }] },
      "all-blank entry" => { "projects" => [{ "name" => "  ", "highlights" => [] }] },
      "empty profile" => { "basics" => { "profiles" => [{}] } },
      "blank profile" => {
        "basics" => { "profiles" => [{ "network" => " ", "username" => "", "url" => "" }] },
      },
      "url-only profile" => {
        "basics" => { "profiles" => [{ "url" => "https://profiles.example.test/ghost" }] },
      },
    }
    JsonResumeContract::SECTIONS.each do |section|
      invalid_documents["empty #{section} entry"] = { section => [{}] }
    end
    {
      "work" => %w[location startDate endDate date],
      "volunteer" => %w[location startDate endDate date],
      "education" => %w[location startDate endDate date],
      "awards" => %w[date],
      "certificates" => %w[date],
      "publications" => %w[releaseDate date],
      "projects" => %w[startDate endDate start_date end_date date],
    }.each do |section, metadata_fields|
      metadata_fields.each do |field|
        invalid_documents["#{section} #{field}-only entry"] = { section => [{ field => "2025" }] }
      end
    end
    invalid_documents["language fluency-only entry"] = {
      "languages" => [{ "fluency" => "Fluent" }],
    }

    invalid_documents.each do |label, resume|
      error = assert_raises(Jekyll::Errors::FatalException, label) do
        JsonResumeContract.validate!(resume)
      end
      assert_includes error.message, "Invalid _data/resume.json"
    end
  end

  def test_complete_jsonresume_fixture_passes_the_build_time_data_contract
    resume = JSON.parse(File.read(File.join(FIXTURES, "cv_jsonresume_contract.json")))

    assert_same resume, JsonResumeContract.validate!(resume)
  end

  def test_jsonresume_generator_enforces_the_contract_during_a_real_build_phase
    @site.data["resume"] = { "work" => "not-an-array" }

    error = assert_raises(Jekyll::Errors::FatalException) do
      JsonResumeContractGenerator.new.generate(@site)
    end
    assert_includes error.message, "Invalid _data/resume.json"
  end

  def test_jsonresume_renders_url_less_publication_name
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")

    assert_includes section_text(document, "Publications"), "A URL-less JSON Resume paper"
  end

  def test_jsonresume_renders_project_description
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")

    assert_includes section_text(document, "Projects"), "The standard project description should remain visible."
  end

  def test_jsonresume_renders_every_standard_section_without_data_loss
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")
    expected_section_text = {
      "Experience" => [
        "Example Labs",
        "Sydney, Australia",
        "An analytical systems laboratory.",
        "Volunteer Maintainer",
        "Improved keyboard navigation.",
      ],
      "Education" => ["Example University", "PhD", "Computer Science", "4.0/4.0", "Reliable Systems", "Inclusive Design"],
      "Awards" => ["JSON Resume Community Award", "2025-06", "Example Foundation"],
      "Publications" => [
        "A URL-less JSON Resume paper",
        "Publication summary remains visible.",
        "A linked JSON Resume paper",
        "2025-11",
        "Example Proceedings",
        "All standard publication fields remain visible.",
      ],
      "Skills" => ["Reliability Engineering", "Advanced", "Testing", "Accessibility"],
      "Languages" => ["English", "Fluent"],
      "Interests" => ["Human-centered Systems", "Inclusive Design", "Reproducibility"],
      "Certificates" => ["Accessible Web Practitioner", "Example Standards Body", "2025-03"],
      "Projects" => [
        "Schema Project",
        "2024-02",
        "2026-07",
        "Preserved standard JSON Resume project fields.",
        "JSON Schema",
        "Liquid",
        "Team Lead",
        "Reviewer",
        "Open Standards Lab",
        "application",
      ],
      "References" => ["Grace Hopper", "Ada builds dependable systems."],
    }

    expected_section_text.each do |section_title, expected_values|
      text = section_text(document, section_title)
      expected_values.each { |value| assert_includes text, value }
    end
  end

  def test_jsonresume_authored_dates_are_not_truncated
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")
    auto_detected_document = render_cv_data(
      JSON.parse(File.read(File.join(FIXTURES, "cv_jsonresume_contract.json"))),
      format: "jsonresume",
      page: { "cv_format" => nil }
    )
    expected_dates = {
      "Research Engineer" => "2024-01 - 2026-01",
      "Volunteer Maintainer" => "2023-01 - 2023-12",
      "JSON Resume Community Award" => "2025-06",
      "A linked JSON Resume paper" => "2025-11",
      "Accessible Web Practitioner" => "2025-03",
      "Schema Project" => "2024-02 - 2026-07",
    }

    expected_dates.each do |entry_title, expected_date|
      entry = heading(document, entry_title)&.ancestors("li")&.first
      refute_nil entry, "expected a semantic entry for #{entry_title}"
      assert_includes normalized_text(entry), expected_date
      auto_detected_entry = heading(auto_detected_document, entry_title)&.ancestors("li")&.first
      refute_nil auto_detected_entry, "auto-detection must keep #{entry_title}"
      assert_includes normalized_text(auto_detected_entry), expected_date
    end
  end

  def test_jsonresume_email_phone_and_website_are_actionable_safe_links
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")
    contact = heading(document, "Contact Information").parent

    assert_equal "ada@example.test", contact.at_css('a[href="mailto:ada@example.test"]')&.text
    assert_equal "+61 2 0000 0000", contact.at_css('a[href="tel:+61200000000"]')&.text
    website = contact.at_css('a[href="https://ada.example.test"]')
    refute_nil website
    assert_equal "_blank", website["target"]
    assert_equal %w[noopener noreferrer], website["rel"]&.split
  end

  def test_jsonresume_empty_optional_fields_do_not_create_placeholders
    document = render_cv_data(
      {
        "basics" => { "summary" => "Summary without contact details.", "location" => {} },
        "work" => [{ "name" => "Undated Work", "position" => "Engineer", "startDate" => "", "endDate" => "", "location" => "" }],
        "certificates" => [{ "name" => "Undated Certificate", "date" => "", "url" => "" }],
        "projects" => [{ "name" => "Undated Project", "startDate" => "", "endDate" => "", "url" => "" }],
      },
      format: "jsonresume"
    )

    assert_nil heading(document, "Contact Information")
    assert_empty document.css(".cv .badge")
    assert_empty document.css(".cv .location")
    assert_includes normalized_text(document), "Summary without contact details."
    assert_includes normalized_text(document), "Undated Work"
    assert_includes normalized_text(document), "Undated Certificate"
    assert_includes normalized_text(document), "Undated Project"
  end

  def test_blank_top_level_summaries_do_not_create_ghost_cards
    jsonresume = render_cv_data(
      { "basics" => { "summary" => " \t ", "location" => {}, "profiles" => [] } },
      format: "jsonresume"
    )
    rendercv = render_cv_data(
      { "cv" => { "summary" => " \t ", "sections" => {} } },
      format: "rendercv"
    )

    assert_nil heading(jsonresume, "Contact Information")
    assert_nil heading(jsonresume, "Professional Summary")
    assert_nil heading(rendercv, "Professional Summary")
  end

  def test_language_without_fluency_does_not_emit_an_orphan_colon
    document = render_cv_partial(
      "languages.liquid",
      "entries" => [{ "language" => "English", "fluency" => "" }]
    )

    assert_equal "English", normalized_text(document.at_css(".language-item"))
  end

  def test_blank_optional_entry_fields_do_not_emit_empty_spacing_nodes
    jsonresume = render_cv_data(
      {
        "work" => [{ "name" => "Work", "position" => "Engineer", "description" => " ", "summary" => " ", "highlights" => [] }],
        "education" => [{ "institution" => "University", "studyType" => "PhD", "score" => " ", "summary" => " ", "courses" => [], "highlights" => [] }],
        "awards" => [{ "title" => "Award", "awarder" => " ", "summary" => " " }],
        "publications" => [{ "name" => "Paper", "publisher" => " ", "summary" => " " }],
      },
      format: "jsonresume"
    )
    normal = render_cv_partial(
      "normal.liquid",
      "entries" => [{ "name" => "Normal", "summary" => " ", "highlights" => [] }]
    )

    { "JSON Resume" => jsonresume, "RenderCV NormalEntry" => normal }.each do |label, document|
      empty_nodes = document.css("p, .cv-markdown-block, ul.items").select do |node|
        normalized_text(node).empty?
      end
      assert_empty empty_nodes.map(&:to_html), "#{label} emitted empty optional spacing nodes"
    end
  end

  def test_jsonresume_basics_image_and_complete_location_are_rendered
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")
    contact = heading(document, "Contact Information").parent
    photo = contact.at_css("img.cv-photo")

    refute_nil photo
    assert_equal "https://images.example.test/ada-lovelace.png", photo["src"]
    assert_equal "Ada Lovelace portrait", photo["alt"]
    %w[1\ Analytical\ Engine\ Way 2000 Sydney AU NSW].each do |value|
      assert_includes normalized_text(contact), value
    end
  end

  def test_jsonresume_standard_urls_are_preserved_safely
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")
    expected_urls = %w[
      https://ada.example.test
      https://example.test/labs
      https://example.test/open-knowledge
      https://example.test/university
      https://example.test/certificates/accessible-web
      https://example.test/publications/linked-paper
      https://example.test/schema-project
    ]

    expected_urls.each do |url|
      link = document.at_css(%(a[href="#{url}"]))
      refute_nil link, "expected a rendered link for #{url}"
      if link["target"] == "_blank"
        assert_equal %w[noopener noreferrer], link["rel"]&.split
      end
    end
  end

  def test_jsonresume_certificate_and_reference_titles_are_entry_headings
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")

    assert_equal "h3", heading(document, "Accessible Web Practitioner")&.name
    assert_equal "h3", heading(document, "Grace Hopper")&.name
  end

  def test_jsonresume_external_certificate_link_is_safe
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")
    link = heading(document, "Accessible Web Practitioner")&.at_css("a")

    assert_equal "https://example.test/certificates/accessible-web", link&.[]("href")
    assert_equal "_blank", link&.[]("target")
    assert_equal %w[noopener noreferrer], link&.[]("rel")&.split
  end

  def test_jsonresume_heading_outline_is_h1_h2_h3
    document = render_fixture("cv_jsonresume_contract.json", format: "jsonresume")
    headings = document.css("h1, h2, h3, h4, h5, h6")
    levels = headings.map { |heading| heading.name.delete_prefix("h").to_i }

    levels.each_cons(2) { |previous, current| assert_operator current, :<=, previous + 1 }
    assert_equal "h2", heading(document, "Contact Information").name
    assert_equal "h2", heading(document, "Education").name
    assert_equal "h3", heading(document, "PhD").name
    assert_equal "h3", heading(document, "Research Engineer").name
    assert_equal "h3", heading(document, "Volunteer Maintainer").name
    assert_equal "h3", heading(document, "JSON Resume Community Award").name
    assert_equal "h3", heading(document, "A URL-less JSON Resume paper").name
    assert_equal "h3", heading(document, "Accessible Web Practitioner")&.name
    assert_equal "h3", heading(document, "Schema Project").name
    assert_equal "h3", heading(document, "Grace Hopper")&.name
  end

  def test_current_site_visible_facts_match_the_migration_snapshot
    data = load_yaml(File.join(ROOT, "_data", "cv.yml"))
    document = render_cv_data(data, format: "rendercv")
    expected_text = File.read(File.join(FIXTURES, "cv_current_visible_facts.txt")).split.join(" ")
    expected_links = [
      "mailto:lizhw.cs@outlook.com",
      "https://aistats.org/aistats2025/awards.html",
      "https://en.wikipedia.org/wiki/Zhengzhou_University",
      "https://github.com/mtics",
      "https://linkedin.com/in/lizhiwi",
      "https://www.shanghaitech.edu.cn/",
      "https://www.uts.edu.au/research/australian-artificial-intelligence-institute",
    ]

    assert_equal expected_text, semantic_text(document.at_css(".cv"))
    assert_equal expected_links.sort, document.css(".cv a[href]").map { |link| link["href"] }.sort
  end

  def test_local_cv_pdf_link_has_a_content_fingerprint
    document = render_fixture(
      "cv_rendercv_v28_scalar.yml",
      format: "rendercv",
      page: { "cv_pdf" => "/assets/pdf/cv.pdf" }
    )

    href = document.at_css('a[aria-label="Open CV PDF"]')["href"]
    assert_match(%r{\A/assets/pdf/cv\.pdf\?v=[0-9a-f]{32}\z}, href)
  end

  def test_local_cv_pdf_link_preserves_query_and_fragment
    document = render_fixture(
      "cv_rendercv_v28_scalar.yml",
      format: "rendercv",
      page: { "cv_pdf" => "/assets/pdf/cv.pdf?download=1#page=2" }
    )

    digest = Digest::MD5.file(File.join(ROOT, "assets", "pdf", "cv.pdf")).hexdigest
    href = document.at_css('a[aria-label="Open CV PDF"]')["href"]
    assert_equal "/assets/pdf/cv.pdf?download=1&v=#{digest}#page=2", href
  end

  def test_protocol_relative_cv_pdf_link_is_normalized_to_https
    input = "//cdn.example/cv.pdf"
    expected = "https://cdn.example/cv.pdf"
    href = begin
      document = render_fixture(
        "cv_rendercv_v28_scalar.yml",
        format: "rendercv",
        page: { "cv_pdf" => input }
      )
      document.at_css('a[aria-label="Open CV PDF"]')["href"]
    rescue StandardError => error
      "#{error.class}: #{error.message}"
    end

    assert_equal expected, href
  end



  private

  def render_fixture(name, format:, page: {})
    data = if File.extname(name) == ".json"
             JSON.parse(File.read(File.join(FIXTURES, name)))
           else
             load_yaml(File.join(FIXTURES, name))
           end

    render_cv_data(data, format: format, page: page)
  end

  def render_cv_data(data, format:, page: {})
    Nokogiri::HTML.fragment(render_cv_html(data, format: format, page: page))
  end

  def render_cv_html(data, format:, page: {})
    @site.data["cv"] = format == "rendercv" ? data : nil
    @site.data["resume"] = format == "jsonresume" ? data : nil

    page_data = { "title" => "CV", "cv_format" => format }.merge(page)
    payload = @site.site_payload
    payload["page"] = page_data
    template = @site.liquid_renderer.file(TEMPLATE).parse(File.read(TEMPLATE))
    template.render!(payload, registers: { site: @site, page: page_data })
  end

  def render_cv_partial(name, variables)
    payload = @site.site_payload
    variables.each { |key, value| payload[key] = value }
    path = File.join(ROOT, "_includes", "cv", name)
    template = @site.liquid_renderer.file(path).parse(File.read(path))
    html = template.render!(payload, registers: { site: @site })
    Nokogiri::HTML.fragment(html)
  end

  def section_text(document, title)
    section_heading = heading(document, title)
    refute_nil section_heading, "expected heading #{title.inspect}"
    normalized_text(section_heading.parent)
  end

  def heading(document, title)
    document.css("h1, h2, h3, h4, h5, h6").find { |node| normalized_text(node) == title }
  end

  def normalized_text(node)
    node.text.split.join(" ")
  end

  def semantic_text(node)
    node.xpath(".//text()").map(&:text).join(" ").split.join(" ")
  end

  def load_yaml(path)
    YAML.safe_load(File.read(path), permitted_classes: [Date, Time], aliases: true)
  end
end
