package com.example.electricity_management_system.sms;

import com.fasterxml.jackson.databind.JsonNode;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.Set;

/**
 * Sends SMS through the Africa's Talking messaging API
 * (POST {base}/version1/messaging, form fields username, to, message, from).
 */
@Slf4j
public class AfricasTalkingSmsSender implements SmsSender {
    /** Recipient status codes that mean the message was accepted: Processed, Sent, Queued. */
    private static final Set<Integer> ACCEPTED = Set.of(100, 101, 102);

    private final RestClient http;
    private final AfricasTalkingProperties props;

    public AfricasTalkingSmsSender(RestClient.Builder builder, AfricasTalkingProperties props) {
        this.props = props;
        this.http = builder
                .baseUrl(props.resolvedBaseUrl())
                .defaultHeader("apiKey", props.apiKey())
                .defaultHeader("Accept", MediaType.APPLICATION_JSON_VALUE)
                .build();
    }

    @Override
    public void send(String phone, String message) {
        MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
        form.add("username", props.username());
        form.add("to", phone);
        form.add("message", message);
        if (props.senderId() != null && !props.senderId().isBlank()) {
            form.add("from", props.senderId());
        }

        JsonNode body;
        try {
            body = http.post()
                    .uri("/version1/messaging")
                    .contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form)
                    .retrieve()
                    .body(JsonNode.class);
        } catch (RestClientException e) {
            throw new SmsDeliveryException("Africa's Talking request failed: " + e.getMessage(), e);
        }

        JsonNode recipients = body == null ? null : body.path("SMSMessageData").path("Recipients");
        if (recipients == null || !recipients.isArray() || recipients.isEmpty()) {
            String detail = body == null ? "empty response" : body.path("SMSMessageData").path("Message").asText("no recipients");
            throw new SmsDeliveryException("Africa's Talking did not accept the SMS: " + detail);
        }
        JsonNode first = recipients.get(0);
        int code = first.path("statusCode").asInt(-1);
        if (!ACCEPTED.contains(code)) {
            throw new SmsDeliveryException("Africa's Talking rejected the SMS: " + first.path("status").asText() + " (" + code + ")");
        }
        log.debug("SMS accepted by Africa's Talking, messageId={}", first.path("messageId").asText());
    }
}
