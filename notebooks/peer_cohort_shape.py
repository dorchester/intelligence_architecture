# Databricks notebook source
# COMMAND ----------

# MAGIC %md
# MAGIC # Peer Cohort Shape Analysis
# MAGIC
# MAGIC Compute each client's seniority share vector, then measure L1 distance and
# MAGIC cosine similarity against the peer median shape.  Reads the governed L3 tier
# MAGIC only (`derived/workforce_composition` and `derived/peer_benchmarks`) through
# MAGIC Unity Catalog — no raw data, no write path.
# MAGIC
# MAGIC Suppresses any cohort with fewer than 3 contributing clients before output.

# COMMAND ----------

from pyspark.sql import functions as F

MIN_CLIENTS = 3
CATALOG = "intelligence_engine_dev"
SCHEMA = "intelligence_engine"

dbutils.widgets.text("lakehouse_s3_prefix", "", "Lakehouse S3 prefix (s3://bucket)")
lakehouse_s3_prefix = dbutils.widgets.get("lakehouse_s3_prefix")
if not lakehouse_s3_prefix:
    raise ValueError("lakehouse_s3_prefix widget parameter is required")

# COMMAND ----------

# Read governed L3 products.  workforce_composition_all is a catalog view over
# read_files on the derived tier; peer_benchmarks is read directly via
# read_files on the same governed external location.
workforce_composition = spark.read.table(f"{CATALOG}.{SCHEMA}.workforce_composition_all")

peer_benchmarks = spark.sql(
    f"SELECT * FROM read_files("
    f"'{lakehouse_s3_prefix}/derived/peer_benchmarks/',"
    f" format => 'parquet')"
)

# COMMAND ----------

# Build each client's seniority share vector: headcount per seniority level as
# a fraction of that client's total headcount. Absolute counts measure company
# size; shape is what is comparable across different-sized organisations.
client_seniority = (
    workforce_composition
    .groupBy("client_id", "seniority_level")
    .agg(F.sum("headcount").alias("headcount"))
)

client_totals = (
    client_seniority
    .groupBy("client_id")
    .agg(F.sum("headcount").alias("total_headcount"))
)

client_shares = (
    client_seniority
    .join(client_totals, on="client_id")
    .withColumn("headcount_share", F.col("headcount") / F.col("total_headcount"))
    .select("client_id", "seniority_level", "headcount_share")
)

# COMMAND ----------

# Build the peer median shape vector from the benchmarks product (already
# suppressed at MIN_CLIENTS), then compute L1 distance and cosine similarity
# for each client against the median.

# Suppress seniority levels that don't meet the minimum client threshold
peer_median = (
    peer_benchmarks
    .filter(F.col("contributing_clients") >= MIN_CLIENTS)
    .select(
        F.col("seniority_level"),
        F.col("median_headcount_share").alias("median_share"),
        F.col("contributing_clients"),
    )
)

# Join each client's shares against the peer median
client_vs_median = (
    client_shares
    .join(peer_median, on="seniority_level", how="inner")
)

# COMMAND ----------

# Compute distance metrics per client.
# L1 distance: sum of absolute differences between client share and median.
# Cosine similarity: dot(client, median) / (||client|| * ||median||).
client_metrics = (
    client_vs_median
    .groupBy("client_id")
    .agg(
        F.sum(F.abs(F.col("headcount_share") - F.col("median_share"))).alias("l1_distance"),
        F.sum(F.col("headcount_share") * F.col("median_share")).alias("dot_product"),
        F.sum(F.col("headcount_share") ** 2).alias("norm_client_sq"),
        F.sum(F.col("median_share") ** 2).alias("norm_median_sq"),
        F.count("seniority_level").alias("seniority_levels_compared"),
    )
    .withColumn(
        "cosine_similarity",
        F.col("dot_product") / (F.sqrt(F.col("norm_client_sq")) * F.sqrt(F.col("norm_median_sq")))
    )
    .select("client_id", "l1_distance", "cosine_similarity", "seniority_levels_compared")
)

# COMMAND ----------

# Suppress: only emit clients that participated in cohorts with enough peers.
# A client whose only seniority levels had < MIN_CLIENTS peers was already
# excluded by the inner join above; this is the final safety check on the
# count of levels actually compared.
client_shape_result = client_metrics.filter(F.col("seniority_levels_compared") >= 1)

# Per-seniority summary: median shape vector with spread, for downstream use.
seniority_summary = (
    peer_median
    .join(
        peer_benchmarks.select("seniority_level", "min_headcount_share", "max_headcount_share"),
        on="seniority_level",
        how="inner",
    )
    .select(
        "seniority_level",
        "median_share",
        "min_headcount_share",
        "max_headcount_share",
        "contributing_clients",
    )
    .orderBy(F.col("median_share").desc())
)

# COMMAND ----------

# Emit results.  One row per client (keyed by client_id only), plus the
# per-seniority summary as a second output.
client_shape_result.orderBy("client_id").display()
seniority_summary.display()
