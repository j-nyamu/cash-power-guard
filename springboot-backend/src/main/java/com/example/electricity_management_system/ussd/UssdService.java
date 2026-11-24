package com.example.electricity_management_system.ussd;

import com.example.electricity_management_system.dto.PurchasedTokenRegistrationDto;
import com.example.electricity_management_system.model.MeterModel;
import com.example.electricity_management_system.model.PurchasedTokenModel;
import com.example.electricity_management_system.model.UserModel;
import com.example.electricity_management_system.repository.MeterRepository;
import com.example.electricity_management_system.repository.PurchasedTokenRepository;
import com.example.electricity_management_system.repository.UserRepository;
import com.example.electricity_management_system.repository.UssdSessionRepository;
import com.example.electricity_management_system.service.PurchasedTokenServices;
import com.example.electricity_management_system.sms.SmsSender;
import com.example.electricity_management_system.utils.Role;
import com.example.electricity_management_system.utils.SignupChannel;
import com.example.electricity_management_system.utils.TokenFormatter;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.apache.coyote.BadRequestException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.*;

/**
 * USSD menu logic, independent of the aggregator's HTTP format.
 *
 * Flow: an unknown phone number is offered registration (name + national ID). A known number gets a
 * one-time SMS code (valid 5 minutes); 3 wrong codes lock USSD for that user for 15 minutes. Once in,
 * the caller can list meters, buy a token, see recent tokens and check a token's status.
 */
@Slf4j
@Service
public class UssdService {
    static final int MAX_CODE_ATTEMPTS = 3;
    static final Duration LOCK_DURATION = Duration.ofMinutes(15);
    static final Duration CODE_TTL = Duration.ofMinutes(5);
    static final Duration SESSION_TTL = Duration.ofMinutes(10);
    static final int MIN_AMOUNT = 100;
    static final int MAX_AMOUNT = 182_500;
    static final int MAX_LISTED = 5;

    private static final String MAIN_MENU =
            "1. My meters\n2. Buy token\n3. Recent tokens\n4. Check a token\n0. Exit";
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {};

    private final UssdSessionRepository sessions;
    private final UserRepository users;
    private final MeterRepository meters;
    private final PurchasedTokenRepository tokens;
    private final PurchasedTokenServices tokenServices;
    private final SmsSender sms;
    private final ObjectMapper json = new ObjectMapper();

    public UssdService(UssdSessionRepository sessions, UserRepository users, MeterRepository meters,
                       PurchasedTokenRepository tokens, PurchasedTokenServices tokenServices, SmsSender sms) {
        this.sessions = sessions;
        this.users = users;
        this.meters = meters;
        this.tokens = tokens;
        this.tokenServices = tokenServices;
        this.sms = sms;
    }

    /**
     * Handles one callback.
     *
     * @param sessionId aggregator session id
     * @param rawPhone  caller's number as sent by the aggregator
     * @param text      everything typed so far, '*'-separated (empty on the first callback)
     */
    @Transactional
    public UssdReply handle(String sessionId, String rawPhone, String text) {
        String phone = PhoneNumbers.normalize(rawPhone);
        if (sessionId == null || sessionId.isBlank() || sessionId.length() > 64 || phone == null) {
            return UssdReply.end("Sorry, this service is only available for Rwandan mobile numbers.");
        }
        LocalDateTime now = LocalDateTime.now();
        Optional<UssdSessionModel> existing = sessions.findById(sessionId);

        if (existing.isEmpty()) {
            UssdSessionModel session = new UssdSessionModel(sessionId, phone);
            return save(session, start(session, phone, now), now);
        }

        UssdSessionModel session = existing.get();
        if (!session.getPhone().equals(phone)) {
            return UssdReply.end("Session error. Please dial again.");
        }
        if (session.getUpdatedAt().plus(SESSION_TTL).isBefore(now)) {
            sessions.delete(session);
            return UssdReply.end("Session expired. Please dial again.");
        }
        String input = lastInput(text);
        UssdReply reply = switch (session.getState()) {
            case REG_MENU -> regMenu(session, input);
            case REG_NAME -> regName(session, input);
            case REG_NID -> regNid(session, input);
            case REG_CONFIRM -> regConfirm(session, input);
            case LOGIN_CODE -> loginCode(session, input, now);
            case MAIN -> main(session, input);
            case BACK_TO_MAIN -> toMain(session, "");
            case BUY_METER -> buyMeter(session, input);
            case BUY_AMOUNT -> buyAmount(session, input);
            case BUY_CONFIRM -> buyConfirm(session, input);
            case RECENT_METER -> recentMeter(session, input);
            case CHECK_TOKEN -> checkToken(session, input);
        };
        return save(session, reply, now);
    }

    /** Deletes abandoned sessions every 10 minutes. */
    @Scheduled(fixedDelay = 600_000)
    @Transactional
    public void purgeOldSessions() {
        int removed = sessions.deleteOlderThan(LocalDateTime.now().minus(SESSION_TTL));
        if (removed > 0) {
            log.debug("Removed {} expired USSD sessions", removed);
        }
    }

    // ---- start / registration -------------------------------------------------------------

    private UssdReply start(UssdSessionModel session, String phone, LocalDateTime now) {
        Optional<UserModel> user = users.findFirstByPhoneNumberInOrderByIdAsc(PhoneNumbers.variants(phone));
        if (user.isEmpty()) {
            session.setState(UssdState.REG_MENU);
            return UssdReply.con("Welcome to Cash Power.\nThis number is not registered.\n1. Register\n2. Exit");
        }
        UserModel u = user.get();
        if (u.ussdLockedUntil != null && u.ussdLockedUntil.isAfter(now)) {
            long minutes = Math.max(1, Duration.between(now, u.ussdLockedUntil).toMinutes() + 1);
            return UssdReply.end("Too many wrong codes. Try again in " + minutes + " min.");
        }
        String code = String.format("%06d", RANDOM.nextInt(1_000_000));
        Map<String, Object> data = new HashMap<>();
        data.put("pendingUserId", u.id);
        data.put("codeHash", hash(session.getSessionId(), code));
        data.put("codeExpiresAt", now.plus(CODE_TTL).toString());
        setData(session, data);
        session.setState(UssdState.LOGIN_CODE);
        sms.send(phone, "Your Cash Power USSD code is " + code + ". It expires in 5 minutes. Do not share it.");
        return UssdReply.con("We sent a 6-digit code to you by SMS.\nEnter code:");
    }

    private UssdReply regMenu(UssdSessionModel session, String input) {
        if ("1".equals(input)) {
            session.setState(UssdState.REG_NAME);
            return UssdReply.con("Enter your full name:");
        }
        return UssdReply.end("Thank you.");
    }

    private UssdReply regName(UssdSessionModel session, String input) {
        String name = input.trim().replaceAll("\\s+", " ");
        if (name.length() < 2 || name.length() > 100 || !name.matches("[\\p{L} .'-]+")) {
            return UssdReply.con("Invalid name. Use letters only.\nEnter your full name:");
        }
        Map<String, Object> data = data(session);
        data.put("name", name);
        setData(session, data);
        session.setState(UssdState.REG_NID);
        return UssdReply.con("Enter your 16-digit National ID:");
    }

    private UssdReply regNid(UssdSessionModel session, String input) {
        String nid = input.trim();
        if (!nid.matches("[123]\\d{4}[78]\\d{10}")) {
            return UssdReply.con("Invalid National ID.\nEnter your 16-digit National ID:");
        }
        if (users.existsByNationalID(nid)) {
            return UssdReply.end("This National ID is already registered. Use the phone number linked to it.");
        }
        Map<String, Object> data = data(session);
        data.put("nid", nid);
        setData(session, data);
        session.setState(UssdState.REG_CONFIRM);
        return UssdReply.con("Register " + data.get("name") + ", ID ****" + nid.substring(12)
                + "?\n1. Confirm\n2. Cancel");
    }

    private UssdReply regConfirm(UssdSessionModel session, String input) {
        if (!"1".equals(input)) {
            return UssdReply.end("Registration cancelled.");
        }
        Map<String, Object> data = data(session);
        String nid = (String) data.get("nid");
        if (users.existsByNationalID(nid)) {
            return UssdReply.end("This National ID is already registered.");
        }
        UserModel user = new UserModel();
        user.names = (String) data.get("name");
        user.nationalID = nid;
        user.phoneNumber = session.getPhone();
        user.role = Role.ROLE_CUSTOMER;
        user.signupChannel = SignupChannel.USSD;
        user = users.save(user);
        session.setUserId(user.id);
        setData(session, new HashMap<>());
        return toMain(session, "Registered! Ask an agent to link your meter.\n");
    }

    private UssdReply loginCode(UssdSessionModel session, String input, LocalDateTime now) {
        Map<String, Object> data = data(session);
        Long userId = toLong(data.get("pendingUserId"));
        UserModel user = userId == null ? null : users.findById(userId).orElse(null);
        if (user == null) {
            return UssdReply.end("Session error. Please dial again.");
        }
        if (user.ussdLockedUntil != null && user.ussdLockedUntil.isAfter(now)) {
            return UssdReply.end("Too many wrong codes. Try again later.");
        }
        if (LocalDateTime.parse((String) data.get("codeExpiresAt")).isBefore(now)) {
            return UssdReply.end("Code expired. Please dial again for a new code.");
        }
        String expected = (String) data.get("codeHash");
        boolean ok = input.matches("\\d{6}") && MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8),
                hash(session.getSessionId(), input).getBytes(StandardCharsets.UTF_8));
        if (!ok) {
            int attempts = (user.ussdFailedAttempts == null ? 0 : user.ussdFailedAttempts) + 1;
            if (attempts >= MAX_CODE_ATTEMPTS) {
                user.ussdFailedAttempts = 0;
                user.ussdLockedUntil = now.plus(LOCK_DURATION);
                users.save(user);
                return UssdReply.end("Too many wrong codes. USSD is locked for 15 minutes.");
            }
            user.ussdFailedAttempts = attempts;
            users.save(user);
            int left = MAX_CODE_ATTEMPTS - attempts;
            return UssdReply.con("Wrong code. " + left + (left == 1 ? " try" : " tries") + " left.\nEnter code:");
        }
        user.ussdFailedAttempts = 0;
        user.ussdLockedUntil = null;
        users.save(user);
        session.setUserId(user.id);
        setData(session, new HashMap<>());
        String name = user.names == null || user.names.isBlank() ? "" : " " + user.names.split(" ")[0];
        return toMain(session, "Welcome" + name + ".\n");
    }

    // ---- main menu ------------------------------------------------------------------------

    private UssdReply toMain(UssdSessionModel session, String prefix) {
        session.setState(UssdState.MAIN);
        return UssdReply.con(prefix + MAIN_MENU);
    }

    private UssdReply main(UssdSessionModel session, String input) {
        UserModel user = currentUser(session);
        if (user == null) {
            return UssdReply.end("Session error. Please dial again.");
        }
        List<MeterModel> mine = meters.findMeterModelByUser(user);
        switch (input) {
            case "1": {
                if (mine.isEmpty()) {
                    return backToMain(session, "You have no meters yet.");
                }
                StringBuilder sb = new StringBuilder("Your meters:");
                mine.stream().limit(MAX_LISTED).forEach(m -> sb.append('\n').append(m.meterNumber));
                if (mine.size() > MAX_LISTED) {
                    sb.append("\n+").append(mine.size() - MAX_LISTED).append(" more on the web app");
                }
                return backToMain(session, sb.toString());
            }
            case "2":
                return chooseMeter(session, mine, UssdState.BUY_METER, "Buy token for which meter?");
            case "3":
                return chooseMeter(session, mine, UssdState.RECENT_METER, "Recent tokens for which meter?");
            case "4":
                session.setState(UssdState.CHECK_TOKEN);
                return UssdReply.con("Enter the 16-digit token:\n0. Back");
            case "0":
                return UssdReply.end("Thank you for using Cash Power.");
            default:
                return UssdReply.con("Invalid choice.\n" + MAIN_MENU);
        }
    }

    private UssdReply backToMain(UssdSessionModel session, String message) {
        session.setState(UssdState.BACK_TO_MAIN);
        return UssdReply.con(message + "\n0. Back");
    }

    /** Asks which meter to use, or skips the question when the user has exactly one. */
    private UssdReply chooseMeter(UssdSessionModel session, List<MeterModel> mine, UssdState next, String question) {
        if (mine.isEmpty()) {
            return backToMain(session, "You have no meters yet. Ask an agent to link your meter.");
        }
        List<MeterModel> shown = mine.subList(0, Math.min(MAX_LISTED, mine.size()));
        Map<String, Object> data = data(session);
        data.put("meters", shown.stream().map(m -> m.meterNumber).toList());
        setData(session, data);
        if (shown.size() == 1) {
            return afterMeterChosen(session, next, shown.get(0).meterNumber);
        }
        session.setState(next);
        StringBuilder sb = new StringBuilder(question);
        for (int i = 0; i < shown.size(); i++) {
            sb.append('\n').append(i + 1).append(". ").append(shown.get(i).meterNumber);
        }
        return UssdReply.con(sb.append("\n0. Back").toString());
    }

    private UssdReply afterMeterChosen(UssdSessionModel session, UssdState flow, Long meterNumber) {
        Map<String, Object> data = data(session);
        data.put("meter", meterNumber);
        setData(session, data);
        if (flow == UssdState.BUY_METER) {
            session.setState(UssdState.BUY_AMOUNT);
            return UssdReply.con("Meter " + meterNumber + "\nEnter amount in RWF (100 = 1 day, max 182500):\n0. Back");
        }
        return recentTokens(session, meterNumber);
    }

    /** Reads "1".."n" against the meter list saved by {@link #chooseMeter}. */
    private Long pickedMeter(UssdSessionModel session, String input) {
        List<?> listed = (List<?>) data(session).getOrDefault("meters", List.of());
        if (!input.matches("\\d")) {
            return null;
        }
        int i = Integer.parseInt(input);
        return i >= 1 && i <= listed.size() ? toLong(listed.get(i - 1)) : null;
    }

    // ---- buy token ------------------------------------------------------------------------

    private UssdReply buyMeter(UssdSessionModel session, String input) {
        if ("0".equals(input)) {
            return toMain(session, "");
        }
        Long meter = pickedMeter(session, input);
        if (meter == null) {
            return UssdReply.con("Invalid choice. Pick a meter number from the list, or 0 to go back.");
        }
        return afterMeterChosen(session, UssdState.BUY_METER, meter);
    }

    private UssdReply buyAmount(UssdSessionModel session, String input) {
        if ("0".equals(input)) {
            return toMain(session, "");
        }
        String digits = input.replace(",", "").trim();
        int amount;
        try {
            amount = Integer.parseInt(digits);
        } catch (NumberFormatException e) {
            amount = -1;
        }
        if (amount < MIN_AMOUNT || amount > MAX_AMOUNT || amount % 100 != 0) {
            return UssdReply.con("Enter a multiple of 100 between 100 and 182500 RWF:\n0. Back");
        }
        Map<String, Object> data = data(session);
        data.put("amount", amount);
        setData(session, data);
        session.setState(UssdState.BUY_CONFIRM);
        return UssdReply.con("Buy " + amount + " RWF (" + amount / 100 + " days) for meter " + data.get("meter")
                + "?\n1. Confirm\n2. Cancel");
    }

    private UssdReply buyConfirm(UssdSessionModel session, String input) {
        if (!"1".equals(input)) {
            return toMain(session, "Purchase cancelled.\n");
        }
        Map<String, Object> data = data(session);
        Long meterNumber = toLong(data.get("meter"));
        int amount = ((Number) data.get("amount")).intValue();
        UserModel user = currentUser(session);
        MeterModel meter = meterNumber == null ? null : meters.findMeterModelByMeterNumber(meterNumber).orElse(null);
        if (user == null || meter == null || meter.user == null || !Objects.equals(meter.user.id, user.id)) {
            return UssdReply.end("This meter is no longer linked to your account.");
        }
        PurchasedTokenModel token;
        try {
            token = tokenServices.createToken(new PurchasedTokenRegistrationDto(meterNumber, amount));
        } catch (BadRequestException e) {
            return UssdReply.end("Purchase failed: " + e.getMessage());
        }
        String formatted = TokenFormatter.formatToken(token.token);
        sms.send(session.getPhone(), "Cash Power token for meter " + meterNumber + ": " + formatted
                + " (" + token.tokenValueDays + " days, " + amount + " RWF).");
        return UssdReply.end("Token: " + formatted + "\nMeter: " + meterNumber + "\nDays: " + token.tokenValueDays
                + "\nWe also sent it by SMS.");
    }

    // ---- recent tokens / check token -------------------------------------------------------

    private UssdReply recentMeter(UssdSessionModel session, String input) {
        if ("0".equals(input)) {
            return toMain(session, "");
        }
        Long meter = pickedMeter(session, input);
        if (meter == null) {
            return UssdReply.con("Invalid choice. Pick a meter number from the list, or 0 to go back.");
        }
        return recentTokens(session, meter);
    }

    private UssdReply recentTokens(UssdSessionModel session, Long meterNumber) {
        MeterModel meter = meters.findMeterModelByMeterNumber(meterNumber).orElse(null);
        if (meter == null) {
            return backToMain(session, "Meter not found.");
        }
        List<PurchasedTokenModel> list = new ArrayList<>(tokens.findByMeterNumber(meter));
        if (list.isEmpty()) {
            return backToMain(session, "No tokens yet for meter " + meterNumber + ".");
        }
        list.sort(Comparator.comparing((PurchasedTokenModel t) -> t.purchaseDate).reversed());
        StringBuilder sb = new StringBuilder("Meter ").append(meterNumber).append(':');
        list.stream().limit(3).forEach(t -> sb.append('\n').append(TokenFormatter.formatToken(t.token))
                .append(' ').append(t.tokenValueDays).append("d ").append(t.tokenStatus));
        return backToMain(session, sb.toString());
    }

    private UssdReply checkToken(UssdSessionModel session, String input) {
        if ("0".equals(input)) {
            return toMain(session, "");
        }
        String digits = input.replaceAll("[^0-9]", "");
        if (digits.length() != 16) {
            return UssdReply.con("A token has 16 digits. Enter the token:\n0. Back");
        }
        UserModel user = currentUser(session);
        PurchasedTokenModel token = tokens.findByToken(digits).orElse(null);
        // Only reveal tokens on the caller's own meters.
        if (user == null || token == null || token.meterNumber == null || token.meterNumber.user == null
                || !Objects.equals(token.meterNumber.user.id, user.id)) {
            return backToMain(session, "Token not found on your meters.");
        }
        String status = token.tokenStatus == null ? "unknown" : switch (token.tokenStatus) {
            case NEW -> "ready to use";
            case USED -> "already used";
            case EXPIRED -> "expired";
        };
        return backToMain(session, TokenFormatter.formatToken(token.token) + "\nMeter " + token.meterNumber.meterNumber
                + ", " + token.tokenValueDays + " days\nStatus: " + status);
    }

    // ---- helpers ----------------------------------------------------------------------------

    private UserModel currentUser(UssdSessionModel session) {
        return session.getUserId() == null ? null : users.findById(session.getUserId()).orElse(null);
    }

    private UssdReply save(UssdSessionModel session, UssdReply reply, LocalDateTime now) {
        if (reply.isEnd()) {
            if (sessions.existsById(session.getSessionId())) {
                sessions.deleteById(session.getSessionId());
            }
        } else {
            session.setUpdatedAt(now);
            sessions.save(session);
        }
        return reply;
    }

    /** Aggregators send the whole path ("1*Jane Doe*1199..."); the newest answer is the last segment. */
    static String lastInput(String text) {
        if (text == null) {
            return "";
        }
        int star = text.lastIndexOf('*');
        return (star < 0 ? text : text.substring(star + 1)).trim();
    }

    private Map<String, Object> data(UssdSessionModel session) {
        if (session.getData() == null || session.getData().isBlank()) {
            return new HashMap<>();
        }
        try {
            return json.readValue(session.getData(), MAP_TYPE);
        } catch (JsonProcessingException e) {
            return new HashMap<>();
        }
    }

    private void setData(UssdSessionModel session, Map<String, Object> data) {
        try {
            session.setData(json.writeValueAsString(data));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    private static Long toLong(Object value) {
        return value instanceof Number n ? n.longValue() : null;
    }

    private static String hash(String salt, String code) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest((salt + ':' + code).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
