package com.example.electricity_management_system.ussd;

/** Where a caller is in the USSD menu tree. Stored on {@link UssdSessionModel}. */
public enum UssdState {
    REG_MENU,
    REG_NAME,
    REG_NID,
    REG_CONFIRM,
    LOGIN_CODE,
    MAIN,
    BACK_TO_MAIN,
    BUY_METER,
    BUY_AMOUNT,
    BUY_CONFIRM,
    RECENT_METER,
    CHECK_TOKEN
}
