# Task: Add Pet Weight Tracking to Spring PetClinic

Add HTTP endpoints that let an owner record and retrieve a pet's weight history.

## Public API contract

### Record weight

`POST /owners/{ownerId}/pets/{petId}/weight`

Request body:

```json
{
  "weightKg": 4.25,
  "recordDate": "2026-08-10"
}
```

- `weightKg` is required and must be greater than zero.
- `recordDate` is required and uses ISO `yyyy-MM-dd` format.
- Return `201 Created` with JSON containing stable `id`, `petId`, `weightKg`, and `recordDate` fields.
- Return `400 Bad Request` for a missing, zero, negative, or malformed value, without creating a row.
- Return `404 Not Found` when the owner or pet does not exist, or when the pet does not belong to the owner, without creating a row.

### Read history

`GET /owners/{ownerId}/pets/{petId}/weight/history`

- Return `200 OK` with a JSON array of records.
- Each item contains stable `id`, `petId`, `weightKg`, and `recordDate` fields.
- Sort by `recordDate` from newest to oldest.
- Apply the same owner/pet existence and ownership checks; invalid ownership returns `404 Not Found`.

## Persistence contract

- Persist records in the H2 database using a `weight_record` table associated with `pets`.
- Add the H2 schema change under `src/main/resources/db/h2/`.
- The application uses Spring Boot 3.5 and Java 17.

## Completion contract

- `./mvnw compile` succeeds.
- The existing PetClinic test suite still passes.
- Add suitable automated tests for the new behavior.
- Do not change unrelated PetClinic behavior.
