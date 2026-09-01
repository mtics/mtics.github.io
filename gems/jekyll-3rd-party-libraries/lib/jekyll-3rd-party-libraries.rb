Jekyll::Hooks.register :site, :after_init do |site|
  require "fileutils"
  require "nokogiri"
  require "open-uri"
  require "uri"

  # css_parser is needed only when the optional download mode rewrites font CSS.
  # Loading it lazily lets the site use a current, security-fixed release while
  # retaining the normal CDN mode used by this repository.
  def download_and_change_rule_set_url(rule_set, rule, dest, dirname, config, file_types)
    if rule_set[rule].include?("url(")
      url = rule_set[rule].split("url(").last.split(")").first
      url = url[1..-2] if url.start_with?("\"", "'")
      file_name = url.split("/").last.split("?").first
      if file_name.end_with?(*file_types)
        url = URI.join(url, url).to_s unless url.start_with?("https://")
        download_file(url, File.join(dest, file_name))
        previous_rule = rule_set[rule]
        prefix = config["baseurl"] ? config["baseurl"] : ""
        rule_set[rule] = if rule_set[rule].split(" ").length > 1
                           "url(#{File.join(prefix, "assets", "libs", dirname, file_name)}) #{rule_set[rule].split(" ").last}"
                         else
                           "url(#{File.join(prefix, "assets", "libs", dirname, file_name)})"
                         end
        puts "Changed #{previous_rule} to #{rule_set[rule]}"
      end
    end
  end

  def download_file(url, dest)
    return if url.start_with?("|")

    dir = File.dirname(dest)
    FileUtils.mkdir_p(dir) unless File.directory?(dir)
    unless File.file?(dest)
      puts "Downloading #{url} to #{dest}"
      File.open(dest, "wb") { |saved_file| URI(url).open("rb") { |read_file| saved_file.write(read_file.read) } }
      raise "Failed to download #{url} to #{dest}" unless File.file?(dest)
    end
  end

  def download_fonts(url, dest, file_types)
    return if url.start_with?("|")
    unless File.directory?(dest) && !Dir.empty?(dest)
      puts "Downloading fonts from #{url} to #{dest}"
      doc = Nokogiri::HTML(URI(url).open("User-Agent" => "Ruby/#{RUBY_VERSION}"))
      doc.css("a").each do |link|
        file_name = link["href"].split("/").last.split("?").first
        download_file(URI.join(url, link["href"]).to_s, File.join(dest, file_name)) if file_name.end_with?(*file_types)
      end
    end
  end

  def download_images(url, dest, file_types)
    return if url.start_with?("|")
    unless File.directory?(dest) && !Dir.empty?(dest)
      puts "Downloading images from #{url} to #{dest}"
      doc = Nokogiri::HTML(URI(url).open("User-Agent" => "Ruby/#{RUBY_VERSION}"))
      doc.xpath("/html/body/div/div[3]/table/tbody/tr/td[1]/a").each do |link|
        file_name = link["href"].split("/").last.split("?").first
        download_file(URI.join(url, link["href"]).to_s, File.join(dest, file_name)) if file_name.end_with?(*file_types)
      end
    end
  end

  def download_fonts_from_css(config, url, dest, lib_name, file_types)
    return if url.start_with?("|")
    file_name = url.split("/").last.split("?").first
    file_name = "google-fonts.css" if file_name == "css"
    unless File.file?(File.join(dest, file_name))
      puts "Downloading fonts from #{url} to #{dest}"
      doc = Nokogiri::HTML(URI(url).open("User-Agent" => "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"))
      css = CssParser::Parser.new
      css.load_string! doc.document.text
      css.each_rule_set do |rule_set|
        download_and_change_rule_set_url(rule_set, "src", File.join(dest, "fonts"), File.join(lib_name, "fonts"), config, file_types)
      end
      puts "Saving modified css file to #{File.join(dest, file_name)}"
      File.write(File.join(dest, file_name), css.to_s)
    end
    file_name
  end

  site.config.fetch("third_party_libraries", {}).each do |key, value|
    next if key == "download"
    value.fetch("url", {}).each do |type, url|
      if url.is_a?(Hash)
        url.each do |type2, url2|
          if url2.include?("{{version}}")
            site.config["third_party_libraries"][key]["url"][type][type2] = url2.gsub("{{version}}", site.config["third_party_libraries"][key]["version"])
          end
        end
      elsif url.include?("{{version}}")
        site.config["third_party_libraries"][key]["url"][type] = url.gsub("{{version}}", site.config["third_party_libraries"][key]["version"])
      end
    end
  end

  next unless site.config.dig("third_party_libraries", "download")

  require "css_parser"
  font_file_types = ["otf", "ttf", "woff", "woff2"]
  image_file_types = [".gif", ".jpg", ".jpeg", ".png", ".webp"]
  site.config["third_party_libraries"].each do |key, value|
    next if key == "download"
    value.fetch("url", {}).each do |type, url|
      if url.is_a?(Hash)
        url.each do |type2, url2|
          file_name = url2.split("/").last.split("?").first
          dest = File.join(site.source, "assets", "libs", key, file_name)
          download_file(url2, dest)
          prefix = site.config["baseurl"] ? site.config["baseurl"] : ""
          site.config["third_party_libraries"][key]["url"][type][type2] = File.join(prefix, "assets", "libs", key, file_name)
        end
      elsif type == "fonts"
        file_name = url.split("/").last.split("?").first
        if file_name.end_with?("css")
          file_name = download_fonts_from_css(site.config, url, File.join(site.source, "assets", "libs", key), key, font_file_types)
          prefix = site.config["baseurl"] ? site.config["baseurl"] : ""
          site.config["third_party_libraries"][key]["url"][type] = File.join(prefix, "assets", "libs", key, file_name)
        else
          download_fonts(url, File.join(site.source, "assets", "libs", key, site.config["third_party_libraries"][key]["local"][type]), font_file_types)
        end
      elsif type == "images"
        download_images(url, File.join(site.source, "assets", "libs", key, site.config["third_party_libraries"][key]["local"][type]), image_file_types)
      else
        file_name = url.split("/").last.split("?").first
        dest = File.join(site.source, "assets", "libs", key, file_name)
        download_file(url, dest)
        prefix = site.config["baseurl"] ? site.config["baseurl"] : ""
        site.config["third_party_libraries"][key]["url"][type] = File.join(prefix, "assets", "libs", key, file_name)
      end
    end
  end
end
