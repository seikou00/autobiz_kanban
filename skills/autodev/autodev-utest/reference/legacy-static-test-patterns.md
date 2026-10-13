# Legacy Java and static dependency presets

JUnit organizes execution; Mockito isolates collaborators. They are complementary.
Preserve the actual JDK, JUnit generation, resolved dependency versions and runner.

## java-unit with legacy JUnit 4

Use the existing runner and imports; test classes/methods are public in JUnit 4:

```java
import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class CalculationTest {
    @Test
    public void calculatesAssignedBehavior() {
        assertEquals(expected, actualSubjectCall);
    }
}
```

Replace placeholders with actual public behavior. Ordinary collaborator mocking
can use `Mockito.mock` and explicit construction. If using a Mockito JUnit runner,
reuse the runner import supported by the resolved version/existing tests; do not
introduce Jupiter's MockitoExtension into a JUnit 4/Surefire 2.x project.

Class lookup and introspection use the same bound runtime parameters:
`lookup-class` locates the exact class; `inspect-class` reads the corresponding
sources jar when available, otherwise bounded javap. Both cache failures and
results. No repeated extraction or decompilation for unchanged jar fingerprints.

## java-static-inline

Use only when the resolved Mockito version and inline mock maker support the
required class. Mockito 3.4+ exposes `mockStatic`; declaration alone is not an
availability proof. Resolve/probe Byte Buddy and the maker once, never repeatedly
try unrelated versions. For supported versions use a scoped resource:

```java
try (MockedStatic<LoginUserInfUtils> users = Mockito.mockStatic(LoginUserInfUtils.class)) {
    users.when(LoginUserInfUtils::getUser).thenReturn(userFixture);
    // Call the real service and assert the assigned authorization behavior.
}
```

Adapt symbols to actual source. Never mock the subject. Scope the static mock to
the test thread; asynchronous production behavior may require a different seam.

## java-static-legacy

Reuse an already validated JDK/JUnit/PowerMock/Mockito combination and existing
runner examples. An unverified version matrix is not a preset. Do not combine
PowerMock and an inline maker speculatively, upgrade the platform, or perform
an open-ended search for Byte Buddy versions. If the existing combination cannot
resolve in the current offline repository, retain exact missing coordinates.

## Legacy MVC without Spring Boot

Use the existing Spring Test generation and `MockMvcBuilders.standaloneSetup`
when Boot test slices do not exist. Register the actual controller, relevant
advice/converters and explicitly required validators. Mock collaborators, keep
the HTTP behavior under test. Do not add Spring Boot just to use `@WebMvcTest`.
Security/transaction/AOP targets may require the actual minimal application
context/filter chain; a standalone fixture cannot stand in for those semantics.

## java-request-fixture

Only use when all relevant state can be controlled without external services:
create a MockHttpServletRequest, bind RequestContextHolder, prepare real input
values and only the explicitly required cache entries. Save/restore shared maps,
cookies and thread-local state in finally/teardown. Do not reconstruct a real
authentication/network chain as a way around a missing mock capability.

## Database and coverage boundary

H2 is a test-scoped dependency when the actual SQL/dialect permits it. Oracle,
vendor-specific SQL, real transaction semantics and integration behavior can
require another test database. A mapper test proves mapper behavior; it does not
prove the service's login/authorization branches or external-call handling.

## Terminal result

If isolation is unavailable within the single preparation budget, return
`reasonCode=testability_gap` with affected testPoints and precise dependency or
seam requirements. Production seam changes belong to controlled implementation
repair; do not claim source_bug from static observation. Retain all artifacts,
record the blocked target, seal permissible test changes, release the lease.
