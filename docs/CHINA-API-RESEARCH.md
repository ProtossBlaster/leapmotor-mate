# China cloud API research (September 2026)

LeapMotor Mate currently supports the international cloud only, as discussed in
[#285](https://github.com/ProtossBlaster/leapmotor-mate/issues/285). A Chinese
market account and a shared B05 (model year 2026) have since been used to verify
**read-only** access to the China cloud. This page records the observed boundary
for anyone considering a separate China adapter. It does not claim that Mate can
currently sign in to that cloud or that other Chinese models are supported.

The observations come from an authorized account, a captured official iOS app
session, inspection of the unofficial Lingpao Zhikong Android app 3.4.8, and
successful vehicle-list, route, signal, mileage, configuration and gateway-refresh
requests. Personal identifiers, tokens, raw responses and location are excluded.
The [experimental China integration](https://github.com/kerniger/leapmotor-ha/releases/tag/v0.8.0b2)
uses a separate read-only client; its release status is not a validation of Mate.

## Observed request flow

| Step | China cloud endpoint | Evidence and qualification |
| --- | --- | --- |
| SMS request | `GET https://appuser.leapmotor.cn/app-user/applogin/compliance/sendmessagecode` | Seen in the iOS capture and Android app. `phoneNo` is RSA encrypted and Base64url encoded. |
| iOS phone login | `POST https://appuser.leapmotor.cn/app-user/applogin/loginwithphone` | Successful official iOS capture used form fields `deviceID`, `phoneNoCiphertext`, `smsCode`. It did not include `smDeviceId`; that does not establish Android behavior. |
| Gateway token exchange | `POST https://app-gw-global-master.leapmotor.com/base/base-user/account/v1/login` | `identifier` is the legacy account ID, `identifierType=1`, and `security` is the legacy token. This is not a password login. |
| Vehicle list | `GET https://app-gw-global-master.leapmotor.com/app/app-global-service/v1/vehicle/list` | Authenticated response contains owned and shared vehicle groups (`bindcars`, `sharedcars`). |
| Per-vehicle route | `GET https://app-gw-global-master.leapmotor.com/app/app-global-service/v1/vehicle/getCarRoute` | Pass the VIN; validate the returned `appRegion` origin before using it. |
| Signal status | `POST {appRegion}/app/app-signal-service/signal/info/query` | Pass the VIN; the observed B05 response contains `data.signalMap` and a cloud collection time. |
| Gateway refresh | `POST https://app-gw-global-master.leapmotor.com/base/base-user/token/v1/refresh` | A gateway-only refresh succeeded after token expiry. Store the complete rotated session before further reads. |

The gateway uses `x-region: CN` and signature version `2.0`. The captured iOS
request used `deviceType: ios`. Signed requests use the session key obtained at
gateway login, not the international app certificate. The Android app shows a
different phone-login path (`check_login_with_phone`) and a Shumei device ID;
those details must not be applied to the observed iOS login.

## Implications for Mate

Mate's current authentication, host allowlists and certificate setup target the
international cloud. Changing only a base URL would leave login, session renewal,
request signing, routing and response decoding incorrect. A China implementation
would need a separate account/session provider and cloud adapter, with explicit
region selection and independent secrets. Keep vehicle identity and normalized
telemetry as shared application concepts.

Some numeric signal IDs and names overlap with Mate's international parser, but
their state codes cannot be assumed equal. A concrete example is `1149 /
chargeState`: the inspected China app decodes `0` as unplugged, `1` charging,
`2` completed, `3` fault, `4` waiting for a schedule and `6` paused. Mate's
current international parser treats `1` as cable connected and `2` as charging;
it also treats `4` as waiting for a schedule. Reusing that parser for China would
mistake a completed charge for an active one. The China
app also names `47` AC input and `1197` DC input; connection alone does not prove
active charging. Unknown or absent codes should remain unknown.

The observed B05 supplied SoC, range, odometer, speed, climate, door, charge and
tire fields. That establishes field availability for this one vehicle, not a
complete model matrix or the meaning and units of every value. The China app
uses `3260 / expectedMileage` as the T03 range source, but a real China T03
profile has not been verified here. The four tire-pressure fields are named
`2646` front left, `2653` front right, `2660` rear left and `2667` rear right;
the experimental decoder treats them as kPa. Validate values against actual
vehicle data before exposing derived measurements.

Location needs its own review of coordinate system, privacy flags and freshness.
Remote commands, PIN handling, keys, vehicle sharing changes and durable
long-term session renewal have not been validated for Mate. The China beta is
read-only; its successful B05 reads are not evidence that commands or other
models work.
