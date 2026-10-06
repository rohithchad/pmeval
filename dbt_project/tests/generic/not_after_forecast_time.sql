{#- Fails (returns rows) when a feature timestamp is later than the row's forecast_time.
    This is the automated guard against look-ahead: attach it to every timestamp column that
    feeds a forecaster. NULL timestamps pass (no information was used). -#}
{% test not_after_forecast_time(model, column_name, forecast_time_column='forecast_time') %}

select *
from {{ model }}
where {{ column_name }} > {{ forecast_time_column }}

{% endtest %}
