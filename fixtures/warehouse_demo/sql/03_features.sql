-- The same two derived features the Python/R pipelines compute.
-- petal_ratio is CREATED here — this is where a downstream break originates.
CREATE TABLE processed_iris AS
SELECT
    id,
    species,
    sepal_length,
    sepal_width,
    petal_length,
    petal_width,
    sepal_length / sepal_width AS sepal_ratio,
    petal_length / petal_width AS petal_ratio
FROM clean_iris
WHERE petal_width > 0;
