{#- The single definition of "when a forecast is made".
    forecast_time = scheduled release time minus N hours (project var forecast_hours_before,
    default 24). Every model that needs forecast_time calls this macro, so changing the rule
    in one place changes it everywhere. -#}
{% macro forecast_time(release_at) -%}
    ({{ release_at }} - to_hours({{ var('forecast_hours_before') | int }}))
{%- endmacro %}
