-- Drop rows with missing measurements and resolve the species name.
-- Reads:  raw_iris, species_lookup
-- Writes: clean_iris
CREATE TABLE clean_iris AS
SELECT
    r.id,
    r.sepal_length,
    r.sepal_width,
    r.petal_length,
    r.petal_width,
    s.species
FROM raw_iris r
JOIN species_lookup s
  ON r.species_id = s.id
WHERE r.petal_width IS NOT NULL
  AND r.petal_length IS NOT NULL
  AND r.sepal_width IS NOT NULL
  AND r.sepal_length IS NOT NULL;
