{#- Fails (returns rows) when a non-null value is outside [min_value, max_value].
    Used for probabilities, which must lie between 0 and 1. -#}
{% test accepted_range(model, column_name, min_value, max_value) %}

select *
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})

{% endtest %}
