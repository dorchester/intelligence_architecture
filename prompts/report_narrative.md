# Report Narrative Generation Prompt

Given the following workforce analysis metrics for **{{client_name}}**:

```
Total Headcount: {{total_headcount}}
Departments: {{department_count}}
Largest Department: {{largest_department}} ({{largest_department_pct}}%)
Average Tenure: {{avg_tenure_years}} years
Median Tenure: {{median_tenure_years}} years
High Turnover Risk: {{turnover_risk_high_pct}}%
```

Write a 2–3 paragraph executive narrative that:
1. Summarizes the workforce composition.
2. Highlights the most significant pattern or risk.
3. Notes one area that warrants further investigation.

Write in third person. Reference the client by name. Do not invent data points
beyond what is provided.
