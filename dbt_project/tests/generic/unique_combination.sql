{#- Fails (returns rows) when the same combination of columns appears more than once.
    Used for composite grains such as (event_id, market_ticker). -#}
{% test unique_combination(model, combination_of_columns) %}

select
    {{ combination_of_columns | join(', ') }},
    count(*) as row_count
from {{ model }}
group by {{ combination_of_columns | join(', ') }}
having count(*) > 1

{% endtest %}
