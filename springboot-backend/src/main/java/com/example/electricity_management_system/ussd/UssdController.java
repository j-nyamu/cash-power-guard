package com.example.electricity_management_system.ussd;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * Callback for the USSD aggregator (Africa's Talking format: form fields sessionId, serviceCode,
 * phoneNumber, text; plain-text "CON ..."/"END ..." reply).
 *
 * The caller's phone number comes from the aggregator, so this endpoint must not be open to anyone:
 * set ussd.callback-secret and register the callback URL as /api/v1/ussd?secret=THE_SECRET.
 */
@RestController
@RequestMapping("/api/v1/ussd")
@Tag(name = "USSD")
public class UssdController {
    private final UssdService ussdService;
    private final String callbackSecret;

    public UssdController(UssdService ussdService, @Value("${ussd.callback-secret:}") String callbackSecret) {
        this.ussdService = ussdService;
        this.callbackSecret = callbackSecret;
    }

    @Operation(summary = "USSD aggregator callback")
    @PostMapping(consumes = MediaType.APPLICATION_FORM_URLENCODED_VALUE, produces = MediaType.TEXT_PLAIN_VALUE)
    public ResponseEntity<String> callback(@RequestParam String sessionId,
                                           @RequestParam String phoneNumber,
                                           @RequestParam(required = false) String serviceCode,
                                           @RequestParam(required = false, defaultValue = "") String text,
                                           @RequestParam(required = false) String secret) {
        if (!callbackSecret.isEmpty() && (secret == null || !MessageDigest.isEqual(
                callbackSecret.getBytes(StandardCharsets.UTF_8), secret.getBytes(StandardCharsets.UTF_8)))) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).contentType(MediaType.TEXT_PLAIN).body("END Forbidden");
        }
        return ResponseEntity.ok().contentType(MediaType.TEXT_PLAIN)
                .body(ussdService.handle(sessionId, phoneNumber, text).toWire());
    }
}
