# frozen_string_literal: true

require "liquid"

# al_folio_core's news include retains the optional `emojify` call even when
# the emoji plugin is not installed. Register the prior identity behavior
# explicitly so strict_filters can still fail on every other unknown filter.
module LiquidCompatibilityFilters
  def emojify(value)
    value
  end
end

Liquid::Template.register_filter(LiquidCompatibilityFilters)
