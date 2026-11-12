package kr.eartalk.car;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.UUID;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;

final class Api {
    private final SharedPreferences prefs;
    Api(Context context) { prefs = context.getSharedPreferences("eartalk", Context.MODE_PRIVATE); }
    String origin() { return prefs.getString("origin", "http://10.0.2.2:8000"); }
    boolean configured() { return prefs.contains("token"); }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias("eartalk")) return (SecretKey) store.getKey("eartalk", null);
        KeyGenerator generator = KeyGenerator.getInstance("AES", "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder("eartalk", KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes("GCM").setEncryptionPaddings("NoPadding").build());
        return generator.generateKey();
    }

    private String encrypt(String value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        return Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + "." +
                Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
    }

    private String decrypt(String value) throws Exception {
        String[] parts = value.split("\\.");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], 0)));
        return new String(cipher.doFinal(Base64.decode(parts[1], 0)), StandardCharsets.UTF_8);
    }

    void configure(String origin, String token) throws Exception {
        URL url = new URL(origin);
        boolean local = java.util.Arrays.asList("10.0.2.2", "127.0.0.1", "localhost").contains(url.getHost());
        if (!(url.getProtocol().equals("https") || (url.getProtocol().equals("http") && local)) ||
                url.getUserInfo() != null || url.getQuery() != null || url.getRef() != null ||
                !url.getPath().matches("/?") || token.isEmpty()) throw new IOException("서버 주소와 기기 토큰을 확인해 주세요.");
        if (prefs.contains("pending") && !(origin().equals(origin) && decrypt(prefs.getString("token","")).equals(token)))
            throw new IOException("저장된 답변을 기존 계정으로 전송한 후 연결을 변경해 주세요.");
        prefs.edit().putString("origin", origin.replaceAll("/$", "")).putString("token", encrypt(token)).commit();
    }

    Object request(String method, String path, JSONObject body, String key) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(origin() + "/api/v1/" + path).openConnection();
        try {
            conn.setInstanceFollowRedirects(false);
            conn.setConnectTimeout(6000);
            conn.setReadTimeout(12000);
            conn.setRequestMethod(method);
            conn.setRequestProperty("Authorization", "Bearer " + decrypt(prefs.getString("token", "")));
            if (body != null) {
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setRequestProperty("Idempotency-Key", key);
                try (OutputStream out = conn.getOutputStream()) { out.write(body.toString().getBytes(StandardCharsets.UTF_8)); }
            }
            int status = conn.getResponseCode();
            InputStream stream = status < 300 ? conn.getInputStream() : conn.getErrorStream();
            if (stream == null) throw new IOException("HTTP " + status);
            StringBuilder result = new StringBuilder();
            try (BufferedReader in = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = in.readLine()) != null) result.append(line);
            }
            JSONObject envelope = new JSONObject(result.toString());
            if (status >= 300) {
                JSONObject error = envelope.optJSONObject("error");
                throw new IOException("HTTP " + status + (error == null ? "" : " · " + error.optString("code")));
            }
            return envelope.get("data");
        } finally { conn.disconnect(); }
    }

    JSONObject get(String path) throws Exception { return (JSONObject) request("GET", path, null, null); }

    void saveAnswer(String path, JSONObject answer) throws Exception {
        if (prefs.contains("pending")) throw new IOException("먼저 저장된 답변을 전송해 주세요.");
        JSONObject pending = new JSONObject().put("path", path).put("body", answer).put("key", UUID.randomUUID().toString());
        if (!prefs.edit().putString("pending", encrypt(pending.toString())).commit()) throw new IOException("답변 저장 실패");
    }

    boolean hasPendingAnswer() { return prefs.contains("pending"); }
    void discardPendingAnswer() { prefs.edit().remove("pending").apply(); }

    void flushAnswer() throws Exception {
        String stored = prefs.getString("pending", null);
        if (stored == null) return;
        JSONObject p = new JSONObject(decrypt(stored));
        String path = p.getString("path");
        JSONObject body = p.getJSONObject("body");
        try {
            request("POST", path, body, p.getString("key"));
        } catch (IOException error) {
            if (!String.valueOf(error.getMessage()).contains("VERSION_CONFLICT")) throw error;
            JSONObject session = get(path.substring(0,path.lastIndexOf('/')));
            if (!session.optString("phase").equals("awaiting_answer") ||
                    !session.optString("current_item_id").equals(body.getString("session_item_id"))) throw error;
            body.put("expected_version",session.getInt("version"));
            p.put("key",UUID.randomUUID().toString());
            if (!prefs.edit().putString("pending",encrypt(p.toString())).commit()) throw new IOException("답변 저장 실패");
            request("POST",path,body,p.getString("key"));
        }
        prefs.edit().remove("pending").commit();
    }
}
