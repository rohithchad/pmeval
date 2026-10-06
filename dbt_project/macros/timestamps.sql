{#- Convert an ISO 8601 string (for example 2026-07-02T12:30:00Z) to a UTC TIMESTAMP.
    Every timestamp in the silver and gold layers is naive UTC, whatever the session time zone. -#}
{% macro to_utc_timestamp(expr) -%}
    cast(cast({{ expr }} as timestamptz) at time zone 'UTC' as timestamp)
{%- endmacro %}

{#- Convert Unix seconds to a UTC TIMESTAMP. -#}
{% macro unix_to_utc_timestamp(expr) -%}
    cast(to_timestamp(cast({{ expr }} as bigint)) at time zone 'UTC' as timestamp)
{%- endmacro %}

{#- Read one JSON field from the raw `payload` column as text. NULL when missing or JSON null. -#}
{% macro jstr(path, column='payload') -%}
    json_extract_string({{ column }}, '$.{{ path }}')
{%- endmacro %}

{#- Convert a fixed-point dollar string such as '0.4200' to a probability in [0, 1].
    A $1 binary contract priced at p dollars implies probability p. -#}
{% macro dollars_to_probability(expr) -%}
    try_cast({{ expr }} as double)
{%- endmacro %}

{#- Convert a TIMESTAMPTZ column (such as ingested_at) to a naive UTC TIMESTAMP. -#}
{% macro to_utc_naive(expr) -%}
    cast(cast({{ expr }} as timestamptz) at time zone 'UTC' as timestamp)
{%- endmacro %}
