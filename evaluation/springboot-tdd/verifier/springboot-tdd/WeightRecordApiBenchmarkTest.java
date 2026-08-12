package org.springframework.samples.petclinic.owner;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
class WeightRecordApiBenchmarkTest {

	@Autowired
	private MockMvc mockMvc;

	@Autowired
	private JdbcTemplate jdbcTemplate;

	@BeforeEach
	void clearWeightRecords() {
		this.jdbcTemplate.update("DELETE FROM weight_record");
	}

	@Test
	void feature_recordsValidWeight() throws Exception {
		this.mockMvc
			.perform(post("/owners/1/pets/1/weight").contentType(MediaType.APPLICATION_JSON)
				.content("{\"weightKg\":4.25,\"recordDate\":\"2026-08-10\"}"))
			.andExpect(status().isCreated())
			.andExpect(jsonPath("$.id").isNumber())
			.andExpect(jsonPath("$.petId").value(1))
			.andExpect(jsonPath("$.weightKg").value(4.25))
			.andExpect(jsonPath("$.recordDate").value("2026-08-10"));
	}

	@Test
	void feature_missingWeightIsRejectedWithoutSideEffect() throws Exception {
		assertBadRequestWithoutSideEffect("{\"recordDate\":\"2026-08-10\"}");
	}

	@Test
	void feature_missingRecordDateIsRejectedWithoutSideEffect() throws Exception {
		assertBadRequestWithoutSideEffect("{\"weightKg\":4.25}");
	}

	@Test
	void feature_invalidRecordDateIsRejectedWithoutSideEffect() throws Exception {
		assertBadRequestWithoutSideEffect("{\"weightKg\":4.25,\"recordDate\":\"not-a-date\"}");
	}

	@Test
	void feature_zeroWeightIsRejectedWithoutSideEffect() throws Exception {
		assertBadRequestWithoutSideEffect("{\"weightKg\":0,\"recordDate\":\"2026-08-10\"}");
	}

	@Test
	void feature_negativeWeightIsRejectedWithoutSideEffect() throws Exception {
		assertBadRequestWithoutSideEffect("{\"weightKg\":-1.5,\"recordDate\":\"2026-08-10\"}");
	}

	@Test
	void feature_unknownPetIsNotFoundWithoutSideEffect() throws Exception {
		this.mockMvc
			.perform(post("/owners/1/pets/999/weight").contentType(MediaType.APPLICATION_JSON)
				.content("{\"weightKg\":4.25,\"recordDate\":\"2026-08-10\"}"))
			.andExpect(status().isNotFound());
		assertThat(rowCount()).isZero();
	}

	@Test
	void feature_petOwnedByAnotherOwnerIsNotFoundWithoutSideEffect() throws Exception {
		this.mockMvc
			.perform(post("/owners/1/pets/2/weight").contentType(MediaType.APPLICATION_JSON)
				.content("{\"weightKg\":4.25,\"recordDate\":\"2026-08-10\"}"))
			.andExpect(status().isNotFound());
		assertThat(rowCount()).isZero();
	}

	@Test
	void feature_historyReturnsStableJsonFields() throws Exception {
		insertWeight("2026-08-10", 4.25);
		this.mockMvc.perform(get("/owners/1/pets/1/weight/history"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$[0].id").isNumber())
			.andExpect(jsonPath("$[0].petId").value(1))
			.andExpect(jsonPath("$[0].weightKg").value(4.25))
			.andExpect(jsonPath("$[0].recordDate").value("2026-08-10"));
	}

	@Test
	void feature_historyForPetOwnedByAnotherOwnerIsNotFound() throws Exception {
		this.mockMvc.perform(get("/owners/1/pets/2/weight/history")).andExpect(status().isNotFound());
	}

	@Test
	void integration_recordIsPersisted() throws Exception {
		insertWeight("2026-08-10", 4.25);
		assertThat(rowCount()).isEqualTo(1);
		Double weight = this.jdbcTemplate.queryForObject("SELECT weight_kg FROM weight_record", Double.class);
		assertThat(weight).isEqualTo(4.25);
	}

	@Test
	void integration_historyIsNewestFirst() throws Exception {
		insertWeight("2026-07-01", 4.0);
		insertWeight("2026-08-10", 4.25);
		this.mockMvc.perform(get("/owners/1/pets/1/weight/history"))
			.andExpect(status().isOk())
			.andExpect(jsonPath("$[0].recordDate").value("2026-08-10"))
			.andExpect(jsonPath("$[1].recordDate").value("2026-07-01"));
	}

	private void assertBadRequestWithoutSideEffect(String body) throws Exception {
		this.mockMvc.perform(post("/owners/1/pets/1/weight").contentType(MediaType.APPLICATION_JSON).content(body))
			.andExpect(status().isBadRequest());
		assertThat(rowCount()).isZero();
	}

	private void insertWeight(String recordDate, double weightKg) throws Exception {
		this.mockMvc
			.perform(post("/owners/1/pets/1/weight").contentType(MediaType.APPLICATION_JSON)
				.content("{\"weightKg\":" + weightKg + ",\"recordDate\":\"" + recordDate + "\"}"))
			.andExpect(status().isCreated());
	}

	private int rowCount() {
		Integer count = this.jdbcTemplate.queryForObject("SELECT COUNT(*) FROM weight_record", Integer.class);
		return count == null ? 0 : count;
	}

}
