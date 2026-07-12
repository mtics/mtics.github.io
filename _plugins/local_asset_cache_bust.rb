# frozen_string_literal: true

require "digest/md5"
require "jekyll"
require "jekyll-cache-bust"
require "al_folio_core"

# Extends jekyll-cache-bust at the Liquid/site boundary. CSS fingerprints cover
# both compilation roots, while local file fingerprints stay confined to the
# current Site source.
module LocalAssetCacheBust

  class << self
    def fingerprint_url(url, source:)
      value = url.to_s
      return value if value.empty? || value.start_with?("#")
      return "https:#{value}" if value.start_with?("//")
      return value if value.match?(%r{\A[a-z][a-z0-9+.-]*:}i)

      path_with_query, fragment = value.split("#", 2)
      path, query = path_with_query.split("?", 2)
      return value if path.split("/").include?("..")

      expanded_source = File.expand_path(source.to_s)
      real_source = File.realpath(expanded_source)
      expanded_file = File.expand_path(path.sub(%r{\A/+}, ""), expanded_source)
      return value unless confined_path?(expanded_file, expanded_source)
      return value unless File.file?(expanded_file)

      real_file = File.realpath(expanded_file)
      return value unless confined_path?(real_file, real_source)

      parameters = query.to_s.split("&").reject { |parameter| parameter.start_with?("v=") || parameter.empty? }
      parameters << "v=#{Digest::MD5.file(real_file).hexdigest}"
      fingerprinted = "#{path}?#{parameters.join("&")}"
      fragment ? "#{fingerprinted}##{fragment}" : fingerprinted
    rescue ArgumentError, Errno::EACCES, Errno::ELOOP, Errno::ENOENT
      value
    end

    def fingerprint_social_cv!(site)
      socials = site.data["socials"]
      return unless socials.is_a?(Hash) && socials.key?("cv_pdf")

      value = socials["cv_pdf"]
      if value.is_a?(Hash)
        key = value.key?("value") ? "value" : "cv_pdf"
        value[key] = fingerprint_url(value[key], source: site.source) if value[key]
      else
        socials["cv_pdf"] = fingerprint_url(value, source: site.source)
      end
    end

    def css_files_content(source:, theme_root: default_theme_root)
      records = [["local", source], ["theme", theme_root]].flat_map do |label, root|
        css_records_for_root(label: label, root: root)
      end
      records.join
    end

    def css_fingerprint_url(url, source:, theme_root: default_theme_root)
      digest = Digest::MD5.hexdigest(css_files_content(source: source, theme_root: theme_root))
      append_fingerprint(url, digest)
    end

    private

    def css_records_for_root(label:, root:)
      return [] if root.to_s.empty?

      expanded_root = File.expand_path(root.to_s)
      real_root = File.realpath(expanded_root)
      paths = Dir.glob(File.join(expanded_root, "_sass", "**", "*"))
      paths << File.join(expanded_root, "assets", "css", "main.scss")

      paths.uniq.sort.each_with_object([]) do |path, records|
        begin
          expanded_path = File.expand_path(path)
          next unless confined_path?(expanded_path, expanded_root)

          real_path = File.realpath(expanded_path)
          next unless confined_path?(real_path, real_root)
          next unless File.file?(real_path)

          relative_path = expanded_path.delete_prefix("#{expanded_root}#{File::SEPARATOR}")
          records << "#{label}/#{relative_path}\0#{File.binread(real_path)}\0"
        rescue ArgumentError, SystemCallError
          # Broken, unreadable, looping, or concurrently replaced inputs are
          # outside the trusted digest domain for this build.
          next
        end
      end
    rescue ArgumentError, SystemCallError
      []
    end

    def confined_path?(path, root)
      path == root || path.start_with?("#{root}#{File::SEPARATOR}")
    end

    def append_fingerprint(url, digest)
      path_with_query, fragment = url.to_s.split("#", 2)
      path, query = path_with_query.split("?", 2)
      parameters = query.to_s.split("&").reject { |parameter| parameter.start_with?("v=") || parameter.empty? }
      parameters << "v=#{digest}"
      fingerprinted = "#{path}?#{parameters.join('&')}"
      fragment ? "#{fingerprinted}##{fragment}" : fingerprinted
    end

    def default_theme_root
      AlFolioCore::THEME_ROOT if defined?(AlFolioCore::THEME_ROOT)
    end
  end

  class SocialCvFingerprintGenerator < Jekyll::Generator
    safe true
    priority :highest

    def generate(site)
      LocalAssetCacheBust.fingerprint_social_cv!(site)
    end
  end

  module LiquidFilters
    def local_asset_fingerprint(file_name)
      site = @context.registers[:site]
      LocalAssetCacheBust.fingerprint_url(file_name, source: site.source)
    end

  end
end

module Jekyll::CacheBust
  def bust_css_cache(file_name)
    site = @context.registers[:site]
    LocalAssetCacheBust.css_fingerprint_url(file_name, source: site.source)
  end
end

Liquid::Template.register_filter(LocalAssetCacheBust::LiquidFilters)
