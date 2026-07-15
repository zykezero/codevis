-- QA. A THIRD consumer of processed_iris, coupled to the others only by columns.
CREATE VIEW qa_flags AS
SELECT
    id,
    species,
    petal_ratio,
    CASE WHEN petal_ratio <= 0 THEN 1 ELSE 0 END AS bad_petal_ratio
FROM processed_iris;

-- SELECT * — the honest case: we know the table, not the columns.
CREATE VIEW raw_dump AS
SELECT * FROM raw_iris;
