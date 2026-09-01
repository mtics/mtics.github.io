# frozen_string_literal: true

require_relative "lib/jekyll-3rd-party-libraries/version"

Gem::Specification.new do |spec|
  spec.name = "jekyll-3rd-party-libraries"
  spec.version = Jekyll::ThirdPartyLibraries::VERSION
  spec.summary = "Force updating cached files and resources for a Jekyll site"
  spec.description = "A compatibility-maintained copy of jekyll-3rd-party-libraries."
  spec.authors = ["George Corrêa de Araújo"]
  spec.homepage = "https://github.com/george-gca/jekyll-3rd-party-libraries"
  spec.license = "MIT"
  spec.files = Dir["lib/**/*"]
  spec.require_paths = ["lib"]
  spec.required_ruby_version = ">= 2.7"

  spec.add_dependency "jekyll", ">= 3.6", "< 5.0"
  spec.add_dependency "css_parser", ">= 3.0.0"
  spec.add_dependency "nokogiri", ">= 1.8", "< 2.0"
end
