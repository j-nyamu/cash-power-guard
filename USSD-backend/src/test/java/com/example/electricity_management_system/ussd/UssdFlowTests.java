package com.example.electricity_management_system.ussd;

import com.example.electricity_management_system.model.MeterModel;
import com.example.electricity_management_system.model.UserModel;
import com.example.electricity_management_system.repository.MeterRepository;
import com.example.electricity_management_system.repository.PurchasedTokenRepository;
import com.example.electricity_management_system.repository.UserRepository;
import com.example.electricity_management_system.repository.UssdSessionRepository;
import com.example.electricity_management_system.sms.SmsDeliveryException;
import com.example.electricity_management_system.sms.SmsSender;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest(properties = {
        "spring.datasource.url=jdbc:h2:mem:ussd_tests;MODE=PostgreSQL;DB_CLOSE_DELAY=-1",
        "spring.datasource.username=sa",
        "spring.datasource.driver-class-name=org.h2.Driver",
        "ussd.callback-secret=test-secret"
})
@AutoConfigureMockMvc
class UssdFlowTests {

    static final List<String[]> SENT = new ArrayList<>();
    static volatile boolean smsDown = false;

    @TestConfiguration
    static class CapturingSms {
        @Bean
        @Primary
        SmsSender capturingSmsSender() {
            return (phone, message) -> {
                if (smsDown) {
                    throw new SmsDeliveryException("gateway down");
                }
                SENT.add(new String[]{phone, message});
            };
        }
    }

    @Autowired MockMvc mvc;
    @Autowired UserRepository users;
    @Autowired MeterRepository meters;
    @Autowired PurchasedTokenRepository tokens;
    @Autowired UssdSessionRepository sessions;

    @BeforeEach
    void clean() {
        SENT.clear();
        smsDown = false;
        sessions.deleteAll();
        tokens.deleteAll();
        meters.deleteAll();
        users.deleteAll();
    }

    /** Simulates one aggregator callback; {@code text} is the cumulative '*'-joined input. */
    private String dial(String session, String phone, String text) throws Exception {
        return mvc.perform(post("/api/v1/ussd").contentType(MediaType.APPLICATION_FORM_URLENCODED)
                        .param("sessionId", session).param("phoneNumber", phone)
                        .param("serviceCode", "*123#").param("text", text).param("secret", "test-secret"))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
    }

    private String lastCode() {
        Matcher m = Pattern.compile("code is (\\d{6})").matcher(SENT.get(SENT.size() - 1)[1]);
        assertThat(m.find()).isTrue();
        return m.group(1);
    }

    private UserModel userWithMeter(String phone, long meterNumber) {
        UserModel u = new UserModel();
        u.names = "Jane Doe";
        u.phoneNumber = phone;
        u.nationalID = "1199880012345678";
        u = users.save(u);
        MeterModel m = new MeterModel();
        m.meterNumber = meterNumber;
        m.user = u;
        meters.save(m);
        return u;
    }

    @Test
    void rejectsWrongSecret() throws Exception {
        mvc.perform(post("/api/v1/ussd").contentType(MediaType.APPLICATION_FORM_URLENCODED)
                        .param("sessionId", "s").param("phoneNumber", "+250788000001").param("text", "")
                        .param("secret", "nope"))
                .andExpect(status().isForbidden());
    }

    @Test
    void unknownNumberCanRegisterAndReachMainMenu() throws Exception {
        String s = UUID.randomUUID().toString();
        String phone = "+250788000002";
        assertThat(dial(s, phone, "")).startsWith("CON").contains("1. Register");
        assertThat(dial(s, phone, "1")).contains("full name");
        assertThat(dial(s, phone, "1*Eric Mugisha")).contains("National ID");
        assertThat(dial(s, phone, "1*Eric Mugisha*1199580012345678")).contains("ID ****5678").contains("1. Confirm");
        assertThat(dial(s, phone, "1*Eric Mugisha*1199580012345678*1")).startsWith("CON Registered!").contains("2. Buy token");

        UserModel created = users.findFirstByPhoneNumberInOrderByIdAsc(List.of(phone)).orElseThrow();
        assertThat(created.names).isEqualTo("Eric Mugisha");
        assertThat(created.password).isNull();
        assertThat(dial(s, phone, "1*Eric Mugisha*1199580012345678*1*1")).contains("no meters");
    }

    @Test
    void knownNumberLogsInWithSmsCodeAndBuysToken() throws Exception {
        userWithMeter("0788000003", 123456L); // stored in local format; aggregator sends +250
        String s = UUID.randomUUID().toString();
        String phone = "+250788000003";

        assertThat(dial(s, phone, "")).contains("Enter code");
        String code = lastCode();
        assertThat(SENT.get(0)[0]).isEqualTo(phone);
        assertThat(dial(s, phone, code)).startsWith("CON Welcome Jane").contains("1. My meters");
        assertThat(dial(s, phone, code + "*1")).contains("123456");
        assertThat(dial(s, phone, code + "*1*0")).contains("2. Buy token");
        // one meter -> amount prompt directly
        assertThat(dial(s, phone, code + "*1*0*2")).contains("Meter 123456").contains("Enter amount");
        assertThat(dial(s, phone, code + "*1*0*2*150")).contains("multiple of 100");
        assertThat(dial(s, phone, code + "*1*0*2*150*3000")).contains("Buy 3000 RWF (30 days)");
        String done = dial(s, phone, code + "*1*0*2*150*3000*1");
        assertThat(done).startsWith("END Token: ").contains("Days: 30");
        assertThat(tokens.findAll()).hasSize(1);
        assertThat(SENT.get(SENT.size() - 1)[1]).contains("meter 123456");
        assertThat(sessions.findById(s)).isEmpty();
    }

    @Test
    void threeWrongCodesLockUssd() throws Exception {
        userWithMeter("+250788000004", 654321L);
        String s = UUID.randomUUID().toString();
        String phone = "+250788000004";
        dial(s, phone, "");
        String wrong = lastCode().equals("000000") ? "111111" : "000000";
        assertThat(dial(s, phone, wrong)).contains("2 tries left");
        assertThat(dial(s, phone, wrong + "*" + wrong)).contains("1 try left");
        assertThat(dial(s, phone, wrong + "*" + wrong + "*" + wrong)).startsWith("END").contains("locked");

        assertThat(dial(UUID.randomUUID().toString(), phone, "")).startsWith("END Too many wrong codes");
    }

    @Test
    void checkTokenOnlyShowsOwnTokens() throws Exception {
        userWithMeter("+250788000005", 111111L);
        String s = UUID.randomUUID().toString();
        String phone = "+250788000005";
        dial(s, phone, "");
        String code = lastCode();
        dial(s, phone, code);
        assertThat(dial(s, phone, code + "*4")).contains("16-digit");
        assertThat(dial(s, phone, code + "*4*1234-5678-9012-3456")).contains("not found");
    }

    @Test
    void smsFailureEndsLoginCleanly() throws Exception {
        userWithMeter("+250788000006", 222222L);
        smsDown = true;
        String s = UUID.randomUUID().toString();
        assertThat(dial(s, "+250788000006", "")).startsWith("END").contains("could not send your code");
        assertThat(sessions.findById(s)).isEmpty();
    }

    @Test
    void tokenIsStillShownWhenSmsCopyFails() throws Exception {
        userWithMeter("+250788000007", 333333L);
        String s = UUID.randomUUID().toString();
        String phone = "+250788000007";
        dial(s, phone, "");
        String code = lastCode();
        dial(s, phone, code);
        dial(s, phone, code + "*2");
        dial(s, phone, code + "*2*500");
        smsDown = true;
        assertThat(dial(s, phone, code + "*2*500*1")).startsWith("END Token: ").contains("SMS copy failed");
        assertThat(tokens.findAll()).hasSize(1);
    }

    @Test
    void rejectsNonRwandanNumbers() throws Exception {
        assertThat(dial("x", "+254700000000", "")).startsWith("END").contains("Rwandan");
    }
}
