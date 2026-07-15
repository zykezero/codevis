-- One row per species. A SECOND consumer of processed_iris.
CREATE VIEW species_summary AS
SELECT
    species,
    COUNT(*)            AS n,
    AVG(petal_ratio)    AS mean_petal_ratio,
    AVG(sepal_ratio)    AS mean_sepal_ratio,
    MAX(sepal_width)    AS max_sepal_width
FROM processed_iris
GROUP BY species;
