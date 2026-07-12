require "minitest/autorun"
require "liquid"

class AccessibilitySocialContractTest < Minitest::Test
  TEMPLATE_PATH = File.expand_path("../_includes/cv/social_networks.liquid", __dir__)

  def render(rows)
    template = Liquid::Template.parse(File.read(TEMPLATE_PATH))
    template.render!("social_networks" => rows)
  end

  def test_blank_rows_are_omitted_and_special_characters_are_safe
    html = render(
      [
        { "network" => "", "username" => "ghost" },
        { "network" => "GitHub", "username" => "" },
        { "network" => "GitHub", "username" => "name /\"><script>alert(1)</script>" },
        {
          "network" => "Profile <unsafe>",
          "username" => "visible & safe",
          "url" => "https://example.test/?q=\"><script>alert(2)</script>",
        },
      ]
    )

    assert_equal 2, html.scan(/<tr>/).length
    refute_includes html, "https://github.com/\""
    refute_match(/<script>/, html)
    assert_includes html, "https://github.com/name+%2F%22%3E%3Cscript%3Ealert%281%29%3C%2Fscript%3E"
    assert_includes html, "name /&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"
    assert_includes html, "Profile &lt;unsafe&gt;"
    assert_includes html, "https://example.test/?q=&quot;&gt;&lt;script&gt;alert(2)&lt;/script&gt;"
  end
end
