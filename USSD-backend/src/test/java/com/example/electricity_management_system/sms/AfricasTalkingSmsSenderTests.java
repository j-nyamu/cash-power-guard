package com.example.electricity_management_system.sms;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.*;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withStatus;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

class AfricasTalkingSmsSenderTests {

    private MockRestServiceServer server;
    private RestClient.Builder builder;

    @BeforeEach
    void setUp() {
        builder = RestClient.builder();
        server = MockRestServiceServer.bindTo(builder).build();
    }

    private AfricasTalkingSmsSender sender(String username, String senderId) {
        return new AfricasTalkingSmsSender(builder, new AfricasTalkingProperties(username, "test-key", senderId, null));
    }

    private static String reply(int statusCode, String status) {
        return """
                {"SMSMessageData":{"Message":"Sent to 1/1 Total Cost: RWF 20","Recipients":[
                {"statusCode":%d,"number":"+250788000001","status":"%s","cost":"RWF 20","messageId":"ATXid_1"}]}}
                """.formatted(statusCode, status);
    }

    @Test
    void sandboxUsernamePostsFormToSandboxApi() {
        server.expect(requestTo("https://api.sandbox.africastalking.com/version1/messaging"))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("apiKey", "test-key"))
                .andExpect(header("Accept", MediaType.APPLICATION_JSON_VALUE))
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_FORM_URLENCODED))
                .andExpect(content().formDataContains(java.util.Map.of(
                        "username", "sandbox", "to", "+250788000001", "message", "hello")))
                .andRespond(withStatus(HttpStatus.CREATED).contentType(MediaType.APPLICATION_JSON).body(reply(101, "Success")));

        sender("sandbox", "").send("+250788000001", "hello");
        server.verify();
    }

    @Test
    void liveUsernameUsesLiveApiAndSenderId() {
        server.expect(requestTo("https://api.africastalking.com/version1/messaging"))
                .andExpect(content().formDataContains(java.util.Map.of("username", "cashpower", "from", "CASHPOWER")))
                .andRespond(withSuccess(reply(100, "Processed"), MediaType.APPLICATION_JSON));

        sender("cashpower", "CASHPOWER").send("+250788000001", "hello");
        server.verify();
    }

    @Test
    void rejectedRecipientThrows() {
        server.expect(requestTo("https://api.sandbox.africastalking.com/version1/messaging"))
                .andRespond(withSuccess(reply(403, "InvalidPhoneNumber"), MediaType.APPLICATION_JSON));

        assertThatThrownBy(() -> sender("sandbox", null).send("+250788000001", "hello"))
                .isInstanceOf(SmsDeliveryException.class)
                .hasMessageContaining("InvalidPhoneNumber");
    }

    @Test
    void httpErrorThrows() {
        server.expect(requestTo("https://api.sandbox.africastalking.com/version1/messaging"))
                .andRespond(withStatus(HttpStatus.UNAUTHORIZED).body("The supplied authentication is invalid"));

        assertThatThrownBy(() -> sender("sandbox", null).send("+250788000001", "hello"))
                .isInstanceOf(SmsDeliveryException.class);
    }

    @Test
    void configuredOnlyWithUsernameAndKey() {
        assertThat(new AfricasTalkingProperties("sandbox", "k", null, null).isConfigured()).isTrue();
        assertThat(new AfricasTalkingProperties("sandbox", "", null, null).isConfigured()).isFalse();
        assertThat(new AfricasTalkingProperties("", "k", null, null).isConfigured()).isFalse();
    }
}
