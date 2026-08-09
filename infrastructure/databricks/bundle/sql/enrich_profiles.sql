-- Batch LLM enrichment in one statement.
--
-- The view is created by setup.sql over the Unity Catalog external location,
-- so this file carries no storage paths. ai_query handles the per-row model
-- calls - parallelism, queuing and retries are the platform's problem, which
-- is precisely the argument for it over a hand-rolled boto3 loop.
--
-- Model note: the trial workspace's Claude endpoints do not permit batch
-- inference; Llama 3.3 70B does. Production Claude batch goes through a
-- Bedrock external-model endpoint instead.

SELECT
  title,
  department,
  ai_query(
    'databricks-meta-llama-3-3-70b-instruct',
    CONCAT(
      'In exactly five words, name the single most marketable skill in this profile: ',
      to_json(struct(title, skills))
    )
  ) AS marketable_skill
FROM intelligence_engine.sterling_profiles
LIMIT 50;
