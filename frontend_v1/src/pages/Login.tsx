import { useState, useRef, useCallback, KeyboardEvent, ClipboardEvent } from "react";
import { useNavigate } from "react-router-dom";

const AUTH_BASE = "http://127.0.0.1:8001";

// ── Types ──────────────────────────────────────────────────────────────────────
type Screen = "credentials" | "otp" | "forgot_email" | "forgot_otp" | "reset_password";

// ── OTP box component ──────────────────────────────────────────────────────────
const OtpGrid = ({
  otp,
  otpRefs,
  onChange,
  onKeyDown,
  onPaste,
  hasError,
}: {
  otp: string[];
  otpRefs: React.MutableRefObject<(HTMLInputElement | null)[]>;
  onChange: (i: number, v: string) => void;
  onKeyDown: (i: number, e: KeyboardEvent<HTMLInputElement>) => void;
  onPaste: (e: ClipboardEvent<HTMLInputElement>) => void;
  hasError: boolean;
}) => (
  <div className="flex items-center gap-2">
    {otp.map((digit, i) => (
      <span key={i} className="contents">
        {i === 3 && <span className="text-scb-text-3 font-mono text-xl mx-0.5">—</span>}
        <input
          ref={(el) => { otpRefs.current[i] = el; }}
          type="text"
          inputMode="numeric"
          maxLength={1}
          value={digit}
          onChange={(e) => onChange(i, e.target.value)}
          onKeyDown={(e) => onKeyDown(i, e)}
          onPaste={i === 0 ? onPaste : undefined}
          className={`w-[46px] h-[52px] bg-surface-2 border rounded-lg text-scb-text text-xl font-mono font-medium text-center outline-none transition-all focus:border-accent-dim focus:shadow-[0_0_0_3px_hsl(var(--accent-glow)/0.12)] ${
            digit ? "border-accent-dim text-primary" : hasError ? "border-danger" : "border-border"
          }`}
        />
      </span>
    ))}
  </div>
);

// ── Main component ─────────────────────────────────────────────────────────────
const Login = () => {
  const navigate = useNavigate();

  // Screen state
  const [screen, setScreen] = useState<Screen>("credentials");

  // Step 1 – credentials
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [usernameErr, setUsernameErr] = useState("");
  const [passwordErr, setPasswordErr] = useState("");

  // Step 2 – login OTP
  const [loginOtp, setLoginOtp] = useState(["", "", "", "", "", ""]);
  const [loginOtpErr, setLoginOtpErr] = useState("");

  // Forgot password – email step
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotEmailErr, setForgotEmailErr] = useState("");

  // Forgot password – OTP step
  const [resetOtp, setResetOtp] = useState(["", "", "", "", "", ""]);
  const [resetOtpErr, setResetOtpErr] = useState("");

  // Forgot password – new password
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showNewPass, setShowNewPass] = useState(false);
  const [newPassErr, setNewPassErr] = useState("");

  // Shared
  const [loading, setLoading] = useState(false);
  const [apiMsg, setApiMsg] = useState(""); // success / info banners
  const [resendText, setResendText] = useState("Resend");

  const passwordRef = useRef<HTMLInputElement>(null);
  const loginOtpRefs = useRef<(HTMLInputElement | null)[]>([]);
  const resetOtpRefs = useRef<(HTMLInputElement | null)[]>([]);

  // ── API helpers ──────────────────────────────────────────────────────────────
  const post = useCallback(async (path: string, body: object) => {
    const res = await fetch(`${AUTH_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Something went wrong.");
    return data;
  }, []);

  // ── OTP box handlers (shared) ────────────────────────────────────────────────
  // BUG 1 FIX: use functional setState so handlers never close over a stale
  // otp snapshot — fast typing no longer overwrites previous digits.
  const makeOtpHandlers = (
    otp: string[],
    setOtp: React.Dispatch<React.SetStateAction<string[]>>,
    setErr: React.Dispatch<React.SetStateAction<string>>,
    refs: React.MutableRefObject<(HTMLInputElement | null)[]>
  ) => ({
    onChange: (i: number, value: string) => {
      const val = value.replace(/\D/g, "");
      setOtp(prev => {
        const next = [...prev];
        next[i] = val ? val[0] : "";
        return next;
      });
      setErr("");
      if (val && i < 5) refs.current[i + 1]?.focus();
    },
    onKeyDown: (i: number, e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Backspace" && !otp[i] && i > 0) {
        setOtp(prev => {
          const next = [...prev];
          next[i - 1] = "";
          return next;
        });
        refs.current[i - 1]?.focus();
      }
    },
    onPaste: (e: ClipboardEvent<HTMLInputElement>) => {
      e.preventDefault();
      const pasted = e.clipboardData.getData("text").replace(/\D/g, "");
      setOtp(prev => {
        const next = [...prev];
        [...pasted].slice(0, 6).forEach((ch, j) => { next[j] = ch; });
        return next;
      });
    },
  });

  const loginOtpHandlers = makeOtpHandlers(loginOtp, setLoginOtp, setLoginOtpErr, loginOtpRefs);
  const resetOtpHandlers = makeOtpHandlers(resetOtp, setResetOtp, setResetOtpErr, resetOtpRefs);

  // ── Step 1: validate credentials → send OTP email ───────────────────────────
  const handleStep1 = async () => {
    let ok = true;
    setUsernameErr("");
    setPasswordErr("");
    if (!username.trim()) { setUsernameErr("Username is required."); ok = false; }
    if (password.length < 6) { setPasswordErr("Password must be at least 6 characters."); ok = false; }
    if (!ok) return;

    setLoading(true);
    setApiMsg(""); // BUG 5 FIX: clear banner before any transition
    try {
      const data = await post("/auth/login/step1", { username: username.trim(), password });
      setApiMsg(data.message);
      setScreen("otp");
      setTimeout(() => loginOtpRefs.current[0]?.focus(), 100);
    } catch (err: any) {
      setPasswordErr(err.message);
    } finally {
      setLoading(false);
    }
  };

  // ── Step 2: verify login OTP → get JWT → navigate ───────────────────────────
  const handleVerifyLoginOtp = async () => {
    const entered = loginOtp.join("");
    setLoginOtpErr("");
    if (entered.length < 6) { setLoginOtpErr("Please enter the full 6-digit OTP."); return; }

    setLoading(true);
    try {
      const data = await post("/auth/login/step2", { username: username.trim(), otp: entered });
      // Store session
      sessionStorage.setItem("scb_token", data.access_token);
      sessionStorage.setItem("scb_user", data.name);
      sessionStorage.setItem("scb_role", data.role);
      sessionStorage.setItem("scb_permissions", JSON.stringify(data.permissions));
      navigate("/chat");
    } catch (err: any) {
      setLoginOtpErr(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleResendLoginOtp = async () => {
    // BUG 6 FIX: guard against multiple simultaneous resend requests
    if (loading) return;
    setLoading(true);
    try {
      await post("/auth/login/step1", { username: username.trim(), password });
      setLoginOtp(["", "", "", "", "", ""]);
      setLoginOtpErr("");
      setApiMsg("OTP resent to your email.");
      setResendText("Sent ✓");
      setTimeout(() => { setResendText("Resend"); setApiMsg(""); }, 3000);
      loginOtpRefs.current[0]?.focus();
    } catch (err: any) {
      setLoginOtpErr(err.message);
    } finally {
      setLoading(false);
    }
  };

  // ── Forgot password: request OTP ─────────────────────────────────────────────
  const handleForgotRequest = async () => {
    setForgotEmailErr("");
    setApiMsg(""); // BUG 5 FIX: clear stale banner from previous screen
    if (!forgotEmail.includes("@")) { setForgotEmailErr("Enter a valid email address."); return; }

    setLoading(true);
    try {
      const data = await post("/auth/forgot-password/request", { email: forgotEmail });
      setApiMsg(data.message);
      setScreen("forgot_otp");
      setTimeout(() => resetOtpRefs.current[0]?.focus(), 100);
    } catch (err: any) {
      setForgotEmailErr(err.message);
    } finally {
      setLoading(false);
    }
  };

  // ── Forgot password: verify OTP → move to reset screen ───────────────────────
  // BUG 3 FIX: previously just did a local length check and advanced the screen
  // without confirming the OTP with the server. Now calls /verify-otp first so
  // an invalid OTP is caught here, not later on the reset screen with a confusing error.
  const handleVerifyResetOtp = async () => {
    const entered = resetOtp.join("");
    setResetOtpErr("");
    if (entered.length < 6) { setResetOtpErr("Please enter the full 6-digit OTP."); return; }

    setLoading(true);
    try {
      await post("/auth/forgot-password/verify-otp", { email: forgotEmail, otp: entered });
      setApiMsg(""); // clear banner before entering password screen
      setScreen("reset_password");
    } catch (err: any) {
      setResetOtpErr(err.message);
    } finally {
      setLoading(false);
    }
  };

  // ── Forgot password: reset ────────────────────────────────────────────────────
  const handleResetPassword = async () => {
    setNewPassErr("");
    if (newPassword.length < 8) { setNewPassErr("Password must be at least 8 characters."); return; }
    if (newPassword !== confirmPassword) { setNewPassErr("Passwords do not match."); return; }

    setLoading(true);
    try {
      const data = await post("/auth/forgot-password/reset", {
        email: forgotEmail,
        otp: resetOtp.join(""),
        new_password: newPassword,
      });
      // BUG 5 FIX: show success banner on the credentials screen, not the reset screen
      setTimeout(() => {
        setScreen("credentials");
        setApiMsg(data.message); // show on login screen after redirect
        setForgotEmail("");
        setResetOtp(["", "", "", "", "", ""]);
        setNewPassword("");
        setConfirmPassword("");
        // Auto-clear the success banner after 4 s
        setTimeout(() => setApiMsg(""), 4000);
      }, 300);
    } catch (err: any) {
      setNewPassErr(err.message);
    } finally {
      setLoading(false);
    }
  };

  // ── Step labels ──────────────────────────────────────────────────────────────
  const stepLabel: Record<Screen, string> = {
    credentials: "STEP 1 OF 2 — CREDENTIALS",
    otp: "STEP 2 OF 2 — VERIFICATION",
    forgot_email: "PASSWORD RESET — EMAIL",
    forgot_otp: "PASSWORD RESET — OTP",
    reset_password: "PASSWORD RESET — NEW PASSWORD",
  };
  // BUG 4 FIX: removed unused stepDot function (dots rendered inline below)

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <div className="flex items-center justify-center min-h-screen overflow-hidden relative">
      {/* Background grid */}
      <div
        className="fixed inset-0 opacity-35 pointer-events-none"
        style={{
          backgroundImage: `linear-gradient(hsl(var(--border)) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--border)) 1px, transparent 1px)`,
          backgroundSize: "48px 48px",
        }}
      />
      <div
        className="fixed pointer-events-none"
        style={{
          top: "-20%", left: "50%", transform: "translateX(-50%)",
          width: 700, height: 700,
          background: "radial-gradient(circle, hsl(var(--accent-glow) / 0.07) 0%, transparent 65%)",
        }}
      />

      <div
        className="relative w-full max-w-[420px] px-4 py-6 flex flex-col items-center gap-7"
        style={{ animation: "fadeUp 0.6s ease both" }}
      >
        {/* Brand */}
        <div className="flex items-center gap-3.5">
          <div className="flex items-center justify-center w-[52px] h-[52px] bg-surface-2 border border-border rounded-lg">
            <svg width="36" height="36" viewBox="0 0 36 36" fill="none">
              <rect x="1" y="1" width="34" height="34" rx="8" stroke="hsl(var(--accent-color))" strokeWidth="1.5"/>
              <path d="M18 8v20M8 18h20" stroke="hsl(var(--accent-color))" strokeWidth="2" strokeLinecap="round"/>
              <circle cx="18" cy="18" r="4" fill="hsl(var(--accent-color))" opacity="0.25"/>
            </svg>
          </div>
          <div>
            <div className="text-lg font-semibold tracking-tight text-scb-text">SecureCareBot</div>
            <div className="text-[0.72rem] font-mono text-primary uppercase tracking-widest mt-0.5">Clinical Intelligence System</div>
          </div>
        </div>

        {/* Card */}
        <div className="w-full bg-surface border border-border rounded-2xl shadow-lg p-8 relative overflow-hidden">
          {/* Top accent line */}
          <div
            className="absolute top-0 left-0 right-0 h-[2px] opacity-60"
            style={{ background: "linear-gradient(90deg, transparent, hsl(var(--accent-color)), transparent)" }}
          />

          {/* Card header */}
          <div className="flex items-center justify-between mb-6">
            <span className="font-mono text-[0.65rem] text-scb-text-3 uppercase tracking-widest">
              {stepLabel[screen]}
            </span>
            {(screen === "credentials" || screen === "otp") && (
              <div className="flex gap-1.5">
                <span className={`w-1.5 h-1.5 rounded-full transition-colors ${screen === "credentials" ? "bg-primary" : "bg-border"}`} />
                <span className={`w-1.5 h-1.5 rounded-full transition-colors ${screen === "otp" ? "bg-primary" : "bg-border"}`} />
              </div>
            )}
          </div>

          {/* ── API message banner ── */}
          {apiMsg && (
            <div className="mb-4 px-3 py-2 bg-primary/10 border border-primary/30 rounded-lg text-[0.78rem] font-mono text-primary">
              {apiMsg}
            </div>
          )}

          {/* ────────────── SCREEN: credentials ────────────── */}
          {screen === "credentials" && (
            <div className="flex flex-col gap-5">
              <h2 className="text-[1.4rem] font-semibold tracking-tight text-scb-text -mb-2">Sign in</h2>
              <p className="text-sm text-scb-text-2 leading-relaxed">
                Enter your credentials. An OTP will be sent to your registered email.
              </p>

              {/* Username */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[0.72rem] font-mono text-scb-text-2 uppercase tracking-wider">Username</label>
                <div className="relative flex items-center">
                  <svg className="absolute left-3 text-scb-text-3 pointer-events-none" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>
                  </svg>
                  <input
                    type="text"
                    value={username}
                    onChange={e => setUsername(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && passwordRef.current?.focus()}
                    className={`w-full py-2.5 pl-10 pr-4 bg-surface-2 border rounded-lg text-scb-text text-sm outline-none transition-all focus:border-accent-dim focus:shadow-[0_0_0_3px_hsl(var(--accent-glow)/0.12)] ${usernameErr ? "border-danger" : "border-border"}`}
                    placeholder="doctor.john"
                    autoComplete="username"
                  />
                </div>
                <span className="text-[0.72rem] font-mono text-danger min-h-[14px]">{usernameErr}</span>
              </div>

              {/* Password */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[0.72rem] font-mono text-scb-text-2 uppercase tracking-wider">Password</label>
                <div className="relative flex items-center">
                  <svg className="absolute left-3 text-scb-text-3 pointer-events-none" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                  </svg>
                  <input
                    ref={passwordRef}
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && handleStep1()}
                    className={`w-full py-2.5 pl-10 pr-10 bg-surface-2 border rounded-lg text-scb-text text-sm outline-none transition-all focus:border-accent-dim focus:shadow-[0_0_0_3px_hsl(var(--accent-glow)/0.12)] ${passwordErr ? "border-danger" : "border-border"}`}
                    placeholder="••••••••"
                    autoComplete="current-password"
                  />
                  <button type="button" onClick={() => setShowPassword(!showPassword)} className="absolute right-3 text-scb-text-3 hover:text-scb-text-2 transition-colors">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                      {showPassword ? (
                        <><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></>
                      ) : (
                        <><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></>
                      )}
                    </svg>
                  </button>
                </div>
                <span className="text-[0.72rem] font-mono text-danger min-h-[14px]">{passwordErr}</span>
              </div>

              <button
                onClick={handleStep1}
                disabled={loading}
                className="w-full py-3 bg-primary text-primary-foreground text-sm font-semibold rounded-lg tracking-wide transition-all hover:opacity-90 hover:shadow-[0_4px_20px_hsl(var(--accent-glow)/0.3)] hover:-translate-y-px active:translate-y-0 mt-1 disabled:opacity-60"
              >
                Continue →
              </button>

              {/* Forgot password link */}
              <button
                type="button"
                onClick={() => { setScreen("forgot_email"); setApiMsg(""); }}
                className="text-[0.76rem] font-mono text-accent-dim hover:text-primary transition-colors text-center"
              >
                Forgot password?
              </button>
            </div>
          )}

          {/* ────────────── SCREEN: login OTP ────────────── */}
          {screen === "otp" && (
            <div className="flex flex-col gap-5">
              <h2 className="text-[1.4rem] font-semibold tracking-tight text-scb-text -mb-2">Verify identity</h2>
              <p className="text-sm text-scb-text-2 leading-relaxed">
                Enter the 6-digit OTP sent to your registered email address.
              </p>

              <OtpGrid
                otp={loginOtp}
                otpRefs={loginOtpRefs}
                onChange={loginOtpHandlers.onChange}
                onKeyDown={loginOtpHandlers.onKeyDown}
                onPaste={loginOtpHandlers.onPaste}
                hasError={!!loginOtpErr}
              />
              <span className="text-[0.72rem] font-mono text-danger min-h-[14px]">{loginOtpErr}</span>

              <div className="flex items-center gap-2 text-[0.75rem] text-scb-text-3">
                Didn't receive it?
                <button onClick={handleResendLoginOtp} className="text-[0.72rem] text-accent-dim underline hover:text-primary transition-colors">
                  {resendText}
                </button>
              </div>

              <button
                onClick={handleVerifyLoginOtp}
                disabled={loading}
                className="w-full py-3 bg-primary text-primary-foreground text-sm font-semibold rounded-lg tracking-wide transition-all hover:opacity-90 hover:shadow-[0_4px_20px_hsl(var(--accent-glow)/0.3)] hover:-translate-y-px active:translate-y-0 mt-1 disabled:opacity-60"
              >
                Verify &amp; Enter →
              </button>
              <button
                onClick={() => { setScreen("credentials"); setLoginOtpErr(""); setApiMsg(""); }}
                className="w-full py-2.5 text-scb-text-2 text-sm border border-border rounded-lg hover:border-border hover:text-scb-text transition-colors -mt-1"
              >
                ← Back
              </button>
            </div>
          )}

          {/* ────────────── SCREEN: forgot – email entry ────────────── */}
          {screen === "forgot_email" && (
            <div className="flex flex-col gap-5">
              <h2 className="text-[1.4rem] font-semibold tracking-tight text-scb-text -mb-2">Reset password</h2>
              <p className="text-sm text-scb-text-2 leading-relaxed">
                Enter your registered email address. We'll send a one-time code to verify it's you.
              </p>

              <div className="flex flex-col gap-1.5">
                <label className="text-[0.72rem] font-mono text-scb-text-2 uppercase tracking-wider">Email address</label>
                <div className="relative flex items-center">
                  <svg className="absolute left-3 text-scb-text-3 pointer-events-none" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <rect x="2" y="4" width="20" height="16" rx="2"/><path d="m2 7 10 7 10-7"/>
                  </svg>
                  <input
                    type="email"
                    value={forgotEmail}
                    onChange={e => setForgotEmail(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && handleForgotRequest()}
                    className={`w-full py-2.5 pl-10 pr-4 bg-surface-2 border rounded-lg text-scb-text text-sm outline-none transition-all focus:border-accent-dim focus:shadow-[0_0_0_3px_hsl(var(--accent-glow)/0.12)] ${forgotEmailErr ? "border-danger" : "border-border"}`}
                    placeholder="doctor@hospital.com"
                    autoComplete="email"
                  />
                </div>
                <span className="text-[0.72rem] font-mono text-danger min-h-[14px]">{forgotEmailErr}</span>
              </div>

              <button
                onClick={handleForgotRequest}
                disabled={loading}
                className="w-full py-3 bg-primary text-primary-foreground text-sm font-semibold rounded-lg tracking-wide transition-all hover:opacity-90 disabled:opacity-60"
              >
                Send OTP →
              </button>
              <button
                onClick={() => { setScreen("credentials"); setForgotEmail(""); setForgotEmailErr(""); setApiMsg(""); }}
                className="w-full py-2.5 text-scb-text-2 text-sm border border-border rounded-lg hover:border-border hover:text-scb-text transition-colors -mt-1"
              >
                ← Back to login
              </button>
            </div>
          )}

          {/* ────────────── SCREEN: forgot – OTP verify ────────────── */}
          {screen === "forgot_otp" && (
            <div className="flex flex-col gap-5">
              <h2 className="text-[1.4rem] font-semibold tracking-tight text-scb-text -mb-2">Enter OTP</h2>
              <p className="text-sm text-scb-text-2 leading-relaxed">
                Enter the 6-digit code sent to <span className="text-primary font-mono">{forgotEmail}</span>.
              </p>

              <OtpGrid
                otp={resetOtp}
                otpRefs={resetOtpRefs}
                onChange={resetOtpHandlers.onChange}
                onKeyDown={resetOtpHandlers.onKeyDown}
                onPaste={resetOtpHandlers.onPaste}
                hasError={!!resetOtpErr}
              />
              <span className="text-[0.72rem] font-mono text-danger min-h-[14px]">{resetOtpErr}</span>

              <div className="flex items-center gap-2 text-[0.75rem] text-scb-text-3">
                Didn't receive it?
                <button
                  onClick={handleForgotRequest}
                  className="text-[0.72rem] text-accent-dim underline hover:text-primary transition-colors"
                >
                  Resend
                </button>
              </div>

              <button
                onClick={handleVerifyResetOtp}
                disabled={loading}
                className="w-full py-3 bg-primary text-primary-foreground text-sm font-semibold rounded-lg tracking-wide transition-all hover:opacity-90 disabled:opacity-60"
              >
                Verify OTP →
              </button>
              <button
                onClick={() => { setScreen("forgot_email"); setResetOtp(["", "", "", "", "", ""]); setResetOtpErr(""); }}
                className="w-full py-2.5 text-scb-text-2 text-sm border border-border rounded-lg hover:border-border hover:text-scb-text transition-colors -mt-1"
              >
                ← Back
              </button>
            </div>
          )}

          {/* ────────────── SCREEN: reset password ────────────── */}
          {screen === "reset_password" && (
            <div className="flex flex-col gap-5">
              <h2 className="text-[1.4rem] font-semibold tracking-tight text-scb-text -mb-2">New password</h2>
              <p className="text-sm text-scb-text-2 leading-relaxed">
                Choose a strong password (min. 8 characters).
              </p>

              {/* New password */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[0.72rem] font-mono text-scb-text-2 uppercase tracking-wider">New password</label>
                <div className="relative flex items-center">
                  <svg className="absolute left-3 text-scb-text-3 pointer-events-none" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                  </svg>
                  <input
                    type={showNewPass ? "text" : "password"}
                    value={newPassword}
                    onChange={e => setNewPassword(e.target.value)}
                    className={`w-full py-2.5 pl-10 pr-10 bg-surface-2 border rounded-lg text-scb-text text-sm outline-none transition-all focus:border-accent-dim focus:shadow-[0_0_0_3px_hsl(var(--accent-glow)/0.12)] ${newPassErr ? "border-danger" : "border-border"}`}
                    placeholder="••••••••"
                  />
                  <button type="button" onClick={() => setShowNewPass(!showNewPass)} className="absolute right-3 text-scb-text-3 hover:text-scb-text-2 transition-colors">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                      {showNewPass
                        ? <><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></>
                        : <><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></>
                      }
                    </svg>
                  </button>
                </div>
              </div>

              {/* Confirm password */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[0.72rem] font-mono text-scb-text-2 uppercase tracking-wider">Confirm password</label>
                <div className="relative flex items-center">
                  <svg className="absolute left-3 text-scb-text-3 pointer-events-none" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                    <rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                  </svg>
                  <input
                    type="password"
                    value={confirmPassword}
                    onChange={e => setConfirmPassword(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && handleResetPassword()}
                    className={`w-full py-2.5 pl-10 pr-4 bg-surface-2 border rounded-lg text-scb-text text-sm outline-none transition-all focus:border-accent-dim focus:shadow-[0_0_0_3px_hsl(var(--accent-glow)/0.12)] ${newPassErr ? "border-danger" : "border-border"}`}
                    placeholder="••••••••"
                  />
                </div>
                <span className="text-[0.72rem] font-mono text-danger min-h-[14px]">{newPassErr}</span>
              </div>

              <button
                onClick={handleResetPassword}
                disabled={loading}
                className="w-full py-3 bg-primary text-primary-foreground text-sm font-semibold rounded-lg tracking-wide transition-all hover:opacity-90 disabled:opacity-60"
              >
                Reset password →
              </button>
              <button
                onClick={() => { setScreen("forgot_otp"); setNewPassErr(""); }}
                className="w-full py-2.5 text-scb-text-2 text-sm border border-border rounded-lg hover:border-border hover:text-scb-text transition-colors -mt-1"
              >
                ← Back
              </button>
            </div>
          )}

          {/* Loading overlay */}
          {loading && (
            <div className="absolute inset-0 bg-surface/90 backdrop-blur-sm flex flex-col items-center justify-center gap-3.5 rounded-2xl z-10">
              <div
                className="w-9 h-9 border-2 border-border rounded-full border-t-primary"
                style={{ animation: "spin 0.8s linear infinite" }}
              />
              <span className="text-sm font-mono text-scb-text-2">
                {screen === "credentials" ? "Authenticating…"
                  : screen === "otp" ? "Verifying OTP…"
                  : screen.startsWith("forgot") || screen === "reset_password" ? "Processing…"
                  : "Loading…"}
              </span>
            </div>
          )}
        </div>

        <p className="text-[0.68rem] font-mono text-scb-text-3 tracking-wider">
          SecureCareBot v1.0 · HIPAA-compliant environment
        </p>
      </div>
    </div>
  );
};

export default Login;