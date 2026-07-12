# frozen_string_literal: true

require "jekyll"

# JSON Resume is rendered from Jekyll's native `_data/resume.json` data. Liquid
# is deliberately permissive, so malformed collection shapes would otherwise
# become empty or "ghost" CV entries instead of failing the build.
module JsonResumeContract
  SECTIONS = %w[
    work
    volunteer
    education
    awards
    certificates
    publications
    skills
    languages
    interests
    references
    projects
  ].freeze

  STRING_ARRAY_FIELDS = {
    "work" => %w[highlights],
    "volunteer" => %w[highlights],
    "education" => %w[courses highlights],
    "skills" => %w[keywords],
    "interests" => %w[keywords],
    "projects" => %w[highlights keywords roles]
  }.freeze

  # A JSON Resume section entry is schema-valid even when it is `{}`. Require
  # content the local partials can render as a subject/body; dates and
  # locations may supplement that content but cannot form an entry alone.
  PRIMARY_CONTENT_FIELDS = {
    "work" => %w[name organization position description summary highlights],
    "volunteer" => %w[organization name position description summary highlights],
    "education" => %w[institution studyType degree area score summary courses highlights],
    "awards" => %w[title name awarder summary],
    "certificates" => %w[name issuer],
    "publications" => %w[name title publisher journal summary doi],
    "skills" => %w[name level keywords],
    "languages" => %w[language name label],
    "interests" => %w[name keywords],
    "references" => %w[name reference],
    "projects" => %w[name title description summary highlights keywords roles entity type]
  }.freeze

  class << self
    def validate!(resume)
      object!(resume, "root")
      optional_object!(resume, "basics", "basics")
      optional_object!(resume, "meta", "meta")

      basics = resume["basics"]
      if basics.is_a?(Hash)
        optional_object!(basics, "location", "basics.location")
        optional_object_array!(basics, "profiles", "basics.profiles")
        validate_profiles!(basics["profiles"]) if basics["profiles"].is_a?(Array)
      end

      SECTIONS.each do |section|
        optional_object_array!(resume, section, section)
        next unless resume[section].is_a?(Array)

        resume[section].each_with_index do |entry, index|
          STRING_ARRAY_FIELDS.fetch(section, []).each do |field|
            optional_string_array!(entry, field, "#{section}[#{index}].#{field}")
          end
          unless PRIMARY_CONTENT_FIELDS.fetch(section).any? { |field| visible_value?(entry[field]) }
            invalid!("#{section}[#{index}]", "an object with non-blank primary renderable content")
          end
        end
      end

      resume
    end

    private

    def invalid!(path, expectation)
      raise Jekyll::Errors::FatalException,
            "Invalid _data/resume.json: #{path} must be #{expectation}"
    end

    def object!(value, path)
      invalid!(path, "an object") unless value.is_a?(Hash)
    end

    def optional_object!(parent, key, path)
      return unless parent.key?(key)

      object!(parent[key], path)
    end

    def optional_object_array!(parent, key, path)
      return unless parent.key?(key)

      entries = parent[key]
      invalid!(path, "an array of objects") unless entries.is_a?(Array)
      entries.each_with_index do |entry, index|
        object!(entry, "#{path}[#{index}]")
      end
    end

    def optional_string_array!(parent, key, path)
      return unless parent.key?(key)

      values = parent[key]
      invalid!(path, "an array of strings") unless values.is_a?(Array)
      values.each_with_index do |value, index|
        invalid!("#{path}[#{index}]", "a string") unless value.is_a?(String)
      end
    end

    def validate_profiles!(profiles)
      profiles.each_with_index do |profile, index|
        next if visible_value?(profile["network"]) && visible_value?(profile["username"])

        invalid!(
          "basics.profiles[#{index}]",
          "an object with non-blank network and username fields"
        )
      end
    end

    def visible_value?(value)
      case value
      when String
        !value.strip.empty?
      when Array
        value.any? { |item| visible_value?(item) }
      when Hash
        value.values.any? { |item| visible_value?(item) }
      else
        !value.nil? && value != false
      end
    end
  end
end

class JsonResumeContractGenerator < Jekyll::Generator
  safe true
  priority :highest

  def generate(site)
    return unless site.data.key?("resume")

    JsonResumeContract.validate!(site.data["resume"])
  end
end
