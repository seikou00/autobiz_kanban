# Test environment profiles

Apply base initialization when the inspector returns `status=init_required`.
An existing `ready` environment may still lack capabilities required by its
selected preset: add the missing test dependency/configuration once, preserving
the actual JUnit generation, build tool and production platform. Declarations
must be confirmed by bounded dependency resolution and actual test execution.
Test-stage POM changes use semantic authorization and are sealed with tests;
they do not require reopening a production task.

## spring-maven-junit

- Add `spring-boot-starter-test` with test scope to `pom.xml` through the existing dependency-management convention.
- For the `security` domain, add `spring-security-test` when it is not already supplied by the project dependency graph.
- Run tests with the repository's Maven wrapper when present, otherwise Maven.
- Legacy JUnit 4 environments are valid. Use their compatible test libraries;
  do not replace them with a Spring Boot starter/Jupiter solely for a template.
- Static mocking requires an actual supported Mockito/maker/Byte Buddy combination;
  PowerMock must reuse a validated legacy combination, not a version search.
- Add H2 or another driver with test scope only for a matching database preset.
  Preserve SQL dialect and service coverage; a mapper test is not a service test.

## spring-gradle-junit

- Add `spring-boot-starter-test` through the existing Gradle DSL and enable JUnit Platform when the project requires it.
- For the `security` domain, add `spring-security-test` when it is not already supplied by the project dependency graph.
- Run tests with the repository's Gradle wrapper when present, otherwise Gradle.

## vue3-vite-vitest

- Add Vitest, `@vue/test-utils`, and the project's chosen DOM environment (`jsdom` or `happy-dom`).
- Add a minimal Vitest config/setup only when the current Vite config cannot host the test settings.
- Add `test:unit` using Vitest when no equivalent unit-test script exists.

## react-vite-vitest

- Add Vitest, React Testing Library, `@testing-library/user-event`, `@testing-library/jest-dom`, and the project's chosen DOM environment (`jsdom` or `happy-dom`).
- Add a minimal Vitest config/setup only when the current Vite config cannot host the test settings.
- Add `test:unit` using Vitest when no equivalent unit-test script exists.

After manifest edits, update only the lock file for the detected `packageManager`, rerun the inspector, then run the smallest test command directly in Bash and register its captured output through `run_utest_command.py --record-only`.
