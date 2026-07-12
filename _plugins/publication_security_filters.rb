# frozen_string_literal: true

require "digest/sha2"
require "liquid"
require "uri"

# Attribute-bound publication data crosses from BibTeX/YAML into URLs, DOM
# identifiers, data attributes, and inline CSS. Keep that boundary pure and
# independently testable rather than relying on HTML escaping alone.
module PublicationSecurityFilters
  DOI_PREFIX = %r{\A(?:doi:\s*|https?://(?:dx\.)?doi\.org/)}i
  DOI_IDENTIFIER = %r{\A10\.\d{4,9}/[^\s"<>\x00-\x1F\x7F]+\z}i
  ATTRIBUTE_UNSAFE = /[\s"'<>\\\x00-\x1F\x7F]/
  PUBLICATION_IDENTIFIER = /\A[A-Za-z0-9][A-Za-z0-9._~:+\/-]*\z/
  SAFE_DOM_ID = /\A[A-Za-z][A-Za-z0-9_.:-]*\z/
  SAFE_CSS_COLOR = /\A(?:#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})|[A-Za-z]{3,20})\z/
  SAFE_CSS_DIMENSION = /\A(?:0|(?:\d+(?:\.\d+)?|\.\d+)(?:px|rem|em|%|vw|vh|vmin|vmax|ch|ex|cm|mm|in|pt|pc))\z/i

  def normalize_doi(value)
    identifier = value.to_s.strip
    loop do
      normalized = identifier.sub(DOI_PREFIX, "").strip
      break if normalized == identifier

      identifier = normalized
    end
    identifier.match?(DOI_IDENTIFIER) ? identifier : ""
  end

  def safe_http_url(value)
    text = value.to_s.strip
    return "" if text.empty? || text.match?(ATTRIBUTE_UNSAFE)

    uri = URI.parse(text)
    return "" unless %w[http https].include?(uri.scheme&.downcase)
    return "" if uri.host.to_s.empty? || uri.userinfo

    text
  rescue URI::Error
    ""
  end

  def safe_link_url(value)
    text = value.to_s.strip
    http_url = safe_http_url(text)
    return http_url unless http_url.empty?
    return "" if text.empty? || text.start_with?("//")
    return "" if text.match?(ATTRIBUTE_UNSAFE)
    return "" if text.match?(%r{\A[a-z][a-z0-9+.-]*:}i)

    uri = URI.parse(text)
    return "" if uri.scheme || uri.host || uri.userinfo
    return "" if uri.path.to_s.split("/").include?("..")

    text
  rescue URI::Error
    ""
  end

  def safe_local_asset_path(value)
    text = value.to_s.strip
    return "" if text.empty? || text.start_with?("/", "//")
    return "" if text.include?("?") || text.include?("#")
    return "" if text.match?(ATTRIBUTE_UNSAFE)
    return "" if text.match?(%r{\A[a-z][a-z0-9+.-]*:}i)
    return "" if text.split("/").any? { |segment| segment.empty? || [".", ".."].include?(segment) }

    text
  end

  def safe_publication_identifier(value)
    text = value.to_s.strip
    return "" unless text.match?(PUBLICATION_IDENTIFIER)
    return "" if text.split("/").any? { |segment| segment.empty? || [".", ".."].include?(segment) }

    text
  end

  def safe_publication_dom_id(value)
    text = value.to_s.strip
    return text if text.match?(SAFE_DOM_ID)

    base = text.downcase.gsub(/[^a-z0-9_.:-]+/, "-").gsub(/\A[-_.:]+|[-_.:]+\z/, "")
    base = "publication" if base.empty?
    base = "publication-#{base}" unless base.match?(/\A[A-Za-z]/)
    digest = Digest::SHA256.hexdigest(text)[0, 12]
    "#{base}-#{digest}"
  end

  def safe_css_color(value)
    text = value.to_s.strip
    text.match?(SAFE_CSS_COLOR) ? text : ""
  end

  def safe_css_dimension(value)
    text = value.to_s.strip
    text.match?(SAFE_CSS_DIMENSION) ? text : ""
  end
end

Liquid::Template.register_filter(PublicationSecurityFilters)
