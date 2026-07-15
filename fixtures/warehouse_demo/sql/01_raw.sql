-- Landing table. The measurements as loaded, before any cleaning.
CREATE TABLE raw_iris (
    id             INTEGER PRIMARY KEY,
    sepal_length   REAL,
    sepal_width    REAL,
    petal_length   REAL,
    petal_width    REAL,
    species_id     INTEGER
);

CREATE TABLE species_lookup (
    id             INTEGER PRIMARY KEY,
    species        TEXT
);
