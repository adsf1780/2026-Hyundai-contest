package kr.eartalk.car;

import android.app.*;
import android.content.*;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.AudioManager;
import android.os.*;
import android.speech.RecognizerIntent;
import android.speech.tts.TextToSpeech;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;

public class MainActivity extends Activity {
    private static final int NAVY = 0xff101c30, CARD = 0xff21334d, MINT = 0xff91ead6, LIGHT = 0xffe5eef8;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private Api api;
    private LinearLayout content;
    private TextToSpeech voice;
    private PleosSpeech vehicleVoice;
    private AudioManager audio;
    private boolean voiceReady, foreground, capturing, busy;
    private JSONObject session, preferences;
    private String recognitionSession;
    private boolean dark, restricted;
    private String pendingPause;
    private DrivingState driving;
    private JSONObject active;
    private String userName = "";
    private static final int INK = 0xff132c26, GREEN = 0xff175747, SURFACE = 0xfff3f6f4;
    private String sectionName = "";
    private int generation;
    private final AudioManager.OnAudioFocusChangeListener focus = change -> {
        if (change < 0) interrupt("audio_focus_loss");
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        api = new Api(this);
        audio = getSystemService(AudioManager.class);
        if (getPackageManager().hasSystemFeature("android.hardware.type.automotive")) {
            vehicleVoice = new PleosSpeech(getApplicationContext(), this::notifySpeech);
            try { driving = new DrivingState(this,this::restrictionChanged); }
            catch (RuntimeException | LinkageError unavailable) { restrictionChanged(true); }
        } else {
            voice = new TextToSpeech(this, status -> voiceReady = status == TextToSpeech.SUCCESS);
        }
        if (!restricted) landing();
    }

    void landing() { if (api.configured()) home(); else connection(); }

    void restrictionChanged(boolean value) {
        if (restricted == value) return;
        restricted = value;
        if (!value) { landing(); return; }
        if (capturing && vehicleVoice == null) finishActivity(7);
        stopSpeech();
        JSONObject current = session;
        page("주행 제한","주차 후 학습을 이어가세요");
        busy=false;
        session=null;
        if (current != null && "active".equals(current.optString("status"))) {
            String sid = current.optString("session_id");
            io.submit(() -> {
                try {
                    JSONObject latest=api.get("sessions/"+sid);
                    if ("active".equals(latest.optString("status")))
                        api.request("POST","sessions/"+sid+"/commands",obj("event_id",id(),"expected_version",latest.getInt("version"),
                            "type","PAUSE","occurred_at",Instant.now().toString(),"payload",obj("reason","driving_restriction")),id());
                } catch (Exception ignored) { /* Fetch server state again on explicit re-entry. */ }
            });
        }
    }

    int dp(int n) { return Math.round(n * getResources().getDisplayMetrics().density); }
    static String id() { return UUID.randomUUID().toString(); }
    static JSONObject obj(Object... pairs) {
        JSONObject value = new JSONObject();
        try {
            for (int i = 0; i < pairs.length; i += 2) value.put((String)pairs[i], pairs[i + 1]);
        } catch (JSONException e) { throw new IllegalArgumentException(e); }
        return value;
    }

    void page(String section, String heading) {
        generation++;
        handler.removeCallbacksAndMessages(null);
        stopSpeech();
        sectionName = section;
        dark = section.startsWith("학습   ") || section.equals("이어하기") || section.equals("학습 결과");
        boolean nav = Arrays.asList("홈","전체 수업","복습","학습 기록","설정").contains(section);
        LinearLayout shell = new LinearLayout(this);
        setContentView(shell);
        if (nav) {
            ScrollView railScroll = new ScrollView(this);
            railScroll.setBackgroundColor(0xfffafcfb);
            LinearLayout rail = new LinearLayout(this);
            rail.setOrientation(LinearLayout.VERTICAL);
            rail.setPadding(dp(10),dp(18),dp(10),0);
            railScroll.addView(rail);
            shell.addView(railScroll,new LinearLayout.LayoutParams(dp(getResources().getConfiguration().screenWidthDp >= 700 ? 156 : 92),-1));
            TextView logo = label("▥  이어톡",20,GREEN);
            logo.setGravity(Gravity.CENTER);
            rail.addView(logo,new LinearLayout.LayoutParams(-1,dp(56)));
            String[] tabs = {"홈","전체 수업","복습","학습 기록","설정"};
            Runnable[] routes = {this::home,this::catalog,this::reviews,this::history,this::settings};
            for (int i=0;i<tabs.length;i++) {
                TextView link = label(tabs[i],16,INK);
                link.setGravity(Gravity.CENTER);
                if (section.equals(tabs[i])) link.setBackground(round(0xffdff1e9,12));
                Runnable route = routes[i];
                link.setOnClickListener(v -> { if (!busy) route.run(); });
                rail.addView(link,new LinearLayout.LayoutParams(-1,dp(52)));
            }
        }
        LinearLayout frame = new LinearLayout(this);
        shell.addView(frame,new LinearLayout.LayoutParams(0,-1,1));
        frame.setOrientation(LinearLayout.VERTICAL);
        frame.setBackgroundColor(dark ? 0xff101e1a : SURFACE);
        boolean historyDashboard = section.equals("학습 기록") && getResources().getConfiguration().screenWidthDp >= 700;
        frame.setPadding(dp(28), dp(historyDashboard ? 10 : 16), dp(28), dp(historyDashboard ? 8 : 12));
        TextView brand = label(nav ? section : "이어톡   /   " + section, 16, dark ? MINT : GREEN);
        frame.addView(brand);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(0, dp(historyDashboard ? 4 : 12), 0, dp(historyDashboard ? 0 : 16));
        if (historyDashboard) {
            frame.addView(content,new LinearLayout.LayoutParams(-1,0,1));
        } else {
            ScrollView scroll = new ScrollView(this);
            frame.addView(scroll);
            scroll.addView(content);
        }
        TextView title = label(heading, historyDashboard ? 24 : 31, dark ? Color.WHITE : INK);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        content.addView(title);
    }

    TextView label(String value, int size, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        return view;
    }

    void line(String value) {
        TextView text = label(value, 20, dark ? LIGHT : INK);
        text.setPadding(0, dp(10), 0, dp(8));
        content.addView(text);
    }

    void button(String name, boolean primary, Runnable action) {
        Button b = new Button(this);
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(primary ? (dark ? MINT : GREEN) : (dark ? 0xff223a33 : Color.WHITE));
        shape.setCornerRadius(dp(15));
        b.setBackground(shape);
        b.setTextColor(primary ? (dark ? INK : Color.WHITE) : (dark ? LIGHT : GREEN));
        b.setTextSize(19);
        b.setAllCaps(false);
        b.setText(name);
        b.setMinHeight(dp(58));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(9);
        content.addView(b, lp);
        b.setOnClickListener(v -> { if (!busy) action.run(); });
    }

    interface Job { Object run() throws Exception; }
    boolean isLoading() { return busy; }
    interface Result { void accept(Object value) throws Exception; }
    void load(Job job, Result done) {
        final int ticket = generation;
        busy = true;
        io.submit(() -> {
            try {
                Object value = job.run();
                runOnUiThread(() -> {
                    if (ticket != generation || isDestroyed()) return;
                    busy = false;
                    try { done.accept(value); } catch (Exception e) { error(e); }
                });
            } catch (Exception e) {
                runOnUiThread(() -> { if (ticket == generation && !isDestroyed()) error(e); });
            }
        });
    }

    void error(Exception e) {
        busy = false;
        session = null;
        page("연결 확인", "학습 상태를 불러오지 못했어요");
        line(Objects.toString(e.getMessage(), "네트워크 연결을 확인해 주세요."));
        button("다시 연결", true, this::home);
        button("서버 설정", false, this::connection);
        if (api.hasPendingAnswer()) button("저장된 답변 관리",false,() -> new AlertDialog.Builder(this)
            .setMessage("이 기기에 남아 있는 미전송 답변을 삭제할까요?")
            .setNegativeButton("보관",null).setPositiveButton("답변 삭제",(d,w) -> {
                api.discardPendingAnswer(); home();
            }).show());
    }

    EditText input(String hint, String value, boolean secret) {
        EditText field = new EditText(this);
        field.setTextColor(INK);
        field.setHintTextColor(0xff52675f);
        field.setSingleLine(true);
        field.setTextSize(19);
        field.setHint(hint);
        field.setInputType(secret ? 129 : 17);
        field.setText(value);
        content.addView(field);
        return field;
    }

    void connection() {
        page("기기 연결", "학습 서버 연결");
        line("계정 발급 시 받은 기기 토큰을 입력해 주세요.");
        EditText address = input("서버 주소", api.origin(), false);
        EditText token = input("기기 토큰", "", true);
        button("연결하기", true, () -> {
            String url = address.getText().toString().trim(), credential = token.getText().toString().trim();
            load(() -> { api.configure(url, credential); return null; }, ignored -> home());
        });
    }

    void home() {
        session = null;
        page("홈", "오늘의 영어 회화");
        line("학습 기록을 불러오는 중…");
        load(() -> {
            api.flushAnswer();
            JSONObject data = api.get("home");
            data.put("viewer",api.get("me").getJSONObject("user"));
            return data;
        }, result -> {
            JSONObject data = (JSONObject) result;
            preferences = data.getJSONObject("preferences");
            active = data.optJSONObject("active_session");
            userName = data.getJSONObject("viewer").optString("display_name");
            JSONArray lessons = data.getJSONArray("recommended_lessons");
            page("홈", "오늘도 가볍게\n영어로 출발해 볼까요?");
            line(userName + "님   ·   하루 목표 " + preferences.getInt("daily_goal_minutes") + "분");
            JSONObject featured = lessons.optJSONObject(0);
            if (active != null || featured != null) {
                LinearLayout panel = new LinearLayout(this);
                panel.setOrientation(LinearLayout.VERTICAL);
                panel.setPadding(dp(24),dp(18),dp(24),dp(20));
                panel.setBackground(round(0xff183f35,22));
                content.addView(panel);
                boolean continuing = active != null;
                panel.addView(label(continuing ? "이어서 학습" : "오늘의 추천",15,MINT));
                TextView headline = label(continuing ? "진행 중인 수업" : featured.getString("title"),28,Color.WHITE);
                headline.setPadding(0,dp(12),0,dp(10));
                panel.addView(headline);
                panel.addView(label(continuing ? active.optInt("current_position")+" / "+active.optInt("total_items")+" 표현" :
                    lessonInfo(featured),17,0xffc4e0d5));
                Button play = new Button(this);
                play.setText(continuing ? "▶   이어서 학습" : "▶   수업 시작");
                play.setTextColor(INK);
                play.setBackground(round(0xffbdf2de,24));
                LinearLayout.LayoutParams playSize = new LinearLayout.LayoutParams(dp(160),dp(48));
                playSize.topMargin = dp(16);
                panel.addView(play,playSize);
                play.setOnClickListener(v -> { if (!busy) openLesson(featured); });
            }
            line("짧게 끝내는 수업");
            lessonCards(lessons);
            JSONObject today = data.getJSONObject("today");
            line("오늘 완료한 수업  " + today.getInt("completed_sessions") + "개    ·    학습한 표현  " + today.getInt("graded_items") + "개");
        });
    }

    GradientDrawable round(int color, int radius) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(color);
        shape.setCornerRadius(dp(radius));
        return shape;
    }
    String lessonInfo(JSONObject lesson) {
        return "약 " + Math.max(1,(lesson.optInt("estimated_seconds")+59)/60) + "분   ·   표현 " + lesson.optInt("item_count") + "개";
    }
    void openLesson(JSONObject lesson) {
        if (active == null) start("lesson",lesson);
        else detail(active.optString("session_id"));
    }
    void chooseSession(String mode, JSONObject lesson) {
        if (active == null) {
            start(mode,lesson);
            return;
        }
        String sid = active.optString("session_id");
        String title = lesson == null ? "복습" : lesson.optString("title");
        new AlertDialog.Builder(this)
            .setTitle("다른 학습을 시작할까요?")
            .setMessage("현재 수업을 중도 종료하고 ‘" + title + "’을 시작합니다. 종료한 수업은 이어서 학습할 수 없습니다.")
            .setPositiveButton("종료하고 시작",(dialog,which) -> {
                if (!busy && !restricted) replaceSession(sid,mode,lesson);
            })
            .setNeutralButton("이어서 학습",(dialog,which) -> {
                if (!busy && !restricted) detail(sid);
            })
            .setNegativeButton("취소",null)
            .show();
    }
    void replaceSession(String sid, String mode, JSONObject lesson) {
        stopSpeech();
        JSONObject body = obj("session_id",id(),"mode",mode,"entry_point","touch");
        load(() -> {
            if (lesson != null) {
                String lessonId = lesson.getString("lesson_id");
                int version = lesson.getInt("version");
                api.get("lessons/" + lessonId + "/versions/" + version);
                body.put("lesson_id",lessonId).put("lesson_version",version);
            }
            api.flushAnswer();
            JSONObject previous = api.get("sessions/" + sid);
            if (Arrays.asList("active","paused").contains(previous.getString("status"))) {
                api.request("POST","sessions/" + sid + "/commands",
                    obj("event_id",id(),"expected_version",previous.getInt("version"),
                        "type","ABANDON","occurred_at",Instant.now().toString(),
                        "payload",obj("reason","discard_progress")),id());
            }
            return api.request("POST","sessions",body,id());
        }, result -> {
            active = null;
            detail(((JSONObject)result).getString("session_id"));
        });
    }
    void lessonCards(JSONArray lessons) throws JSONException {
        int columns = getResources().getConfiguration().screenWidthDp >= 720 ? 2 : 1;
        LinearLayout row = null;
        for (int i=0; i<lessons.length(); i++) {
            if (i % columns == 0) {
                row = new LinearLayout(this);
                content.addView(row);
            }
            JSONObject l = lessons.getJSONObject(i);
            LinearLayout tile = new LinearLayout(this);
            tile.setOrientation(LinearLayout.VERTICAL);
            tile.setPadding(dp(18),dp(16),dp(18),dp(16));
            tile.setBackground(round(Color.WHITE,16));
            LinearLayout.LayoutParams size = new LinearLayout.LayoutParams(0,-2,1);
            size.setMargins(0,dp(8),dp(8),dp(6));
            row.addView(tile,size);
            String topic = l.optString("topic");
            String category = topic.equals("ordering") ? "주문" : topic.equals("introduction") ? "자기소개" :
                    topic.equals("appointments") ? "약속" : topic;
            tile.addView(label(category+"   ·   "+l.optString("level"),14,GREEN));
            TextView name = label(l.getString("title"),21,INK);
            name.setPadding(0,dp(8),0,dp(6));
            tile.addView(name);
            tile.addView(label(lessonInfo(l),15,0xff52675f));
            tile.setFocusable(true);
            tile.setOnClickListener(v -> { if (!busy) chooseSession("lesson",l); });
        }
    }
    void catalog() {
        page("전체 수업","오늘 필요한 영어를 골라보세요");
        load(() -> api.request("GET","lessons?limit=100",null,null), r -> lessonCards((JSONArray)r));
    }
    void reviews() {
        page("복습","다시 만나는 표현");
        load(() -> api.request("GET","review-items?limit=100",null,null), r -> {
            JSONArray list=(JSONArray)r;
            if (list.length()==0) { line("오늘 복습할 표현이 없어요."); return; }
            button("복습 시작",true,() -> chooseSession("review",null));
            for (int i=0;i<list.length();i++) {
                JSONObject item=list.getJSONObject(i);
                line(item.getString("text_en")+"\n"+item.getString("meaning_ko"));
            }
        });
    }
    void history() {
        history(java.time.YearMonth.now());
    }

    void history(java.time.YearMonth month) {
        page("학습 기록","조금씩 쌓인 변화가 보여요");
        TextView loading = label("학습 기록을 불러오는 중…",16,GREEN);
        content.addView(loading);
        java.time.LocalDate today = java.time.LocalDate.now();
        java.time.LocalDate end = month.equals(java.time.YearMonth.from(today)) ? today : month.atEndOfMonth();
        java.time.YearMonth previous = month.minusMonths(1);
        load(() -> obj("current",api.get("progress?from="+month.atDay(1)+"&to="+end),
                "previous",api.get("progress?from="+previous.atDay(1)+"&to="+previous.atEndOfMonth())), r -> {
            content.removeView(loading);
            JSONObject data = (JSONObject)r;
            LearningHistoryView dashboard = new LearningHistoryView(this,month,end,data.getJSONObject("current"),
                    data.getJSONObject("previous"), selected -> { if (!busy) history(selected); });
            boolean wide = getResources().getConfiguration().screenWidthDp >= 700;
            content.addView(dashboard,new LinearLayout.LayoutParams(-1,wide ? 0 : -2,wide ? 1 : 0));
        });
    }
    void settings() {
        page("설정", "학습 설정");
        line("학습 수준");
        for (String level : new String[]{"A1","A2","B1"}) {
            button(level, level.equals(preferences.optString("level")), () -> load(
                () -> api.request("PATCH","me/preferences",obj("expected_version",preferences.optInt("version"),"level",level),id()), r -> home()));
        }
        line("하루 학습 목표");
        for (int minutes : new int[]{5,10,15,20,25,30}) {
            button(minutes+"분",minutes==preferences.optInt("daily_goal_minutes"),() -> load(() ->
                api.request("PATCH","me/preferences",obj("daily_goal_minutes",minutes,"expected_version",preferences.getInt("version")),id()),
                updated -> { preferences=(JSONObject)updated; settings(); }));
        }
        button("기기 연결 변경", false, this::connection);
        button("홈으로", false, this::home);
    }

    void start(String mode, JSONObject lesson) {
        JSONObject body = obj("session_id", id(), "mode", mode, "entry_point", "touch");
        load(() -> {
            if (lesson != null) body.put("lesson_id",lesson.getString("lesson_id")).put("lesson_version",lesson.getInt("version"));
            return api.request("POST","sessions",body,id());
        }, r -> detail(((JSONObject)r).getString("session_id")));
    }

    String path() { return "sessions/" + session.optString("session_id"); }
    void detail(String sid) { load(() -> api.get("sessions/" + sid), r -> renderSession((JSONObject)r)); }

    void command(String type, JSONObject payload, boolean exit) {
        stopSpeech();
        String sid = session.optString("session_id");
        JSONObject body = obj("event_id",id(),"type",type,"payload",payload,"expected_version",session.optInt("version"),
            "occurred_at",Instant.now().toString());
        load(() -> api.request("POST","sessions/"+sid+"/commands",body,id()), r -> { if (exit) home(); else detail(sid); });
    }

    void renderSession(JSONObject s) throws Exception {
        session = s;
        String status = s.getString("status"), sid = s.getString("session_id");
        if (status.equals("completed") || status.equals("abandoned")) {
            String summaryPath = path() + "/summary";
            session = null;
            load(() -> api.get(summaryPath), r -> {
                JSONObject summary = (JSONObject)r;
                page("학습 결과", "수업을 마쳤어요");
                line("완료한 표현   " + summary.getInt("finished_items") + " / " + summary.getInt("total_items"));
                JSONArray words = summary.getJSONArray("expressions");
                for (int i=0; i<words.length(); i++) line(words.getJSONObject(i).getString("text"));
                button("홈으로",true,this::home);
            });
            return;
        }
        if (status.equals("paused")) {
            page("이어하기", "잠시 멈춘 수업");
            JSONObject plan = s.optJSONObject("resume_plan");
            if (plan != null) for (String key : new String[]{"context_prelude_ko","context_text_ko"})
                if (!plan.isNull(key)) line(plan.getString(key));
            button("이어서 학습",true,() -> command("RESUME",obj(),false));
            button("홈으로",false,this::home);
            return;
        }
        if ((!foreground && !capturing) || pendingPause != null) {
            String reason = pendingPause != null ? pendingPause : "app_background";
            pendingPause = null;
            command("PAUSE",obj("reason",reason),false);
            return;
        }
        String phase = s.getString("phase"), item = s.getString("current_item_id");
        JSONObject c = s.getJSONObject("current_item").getJSONObject("content");
        String section = "학습   " + s.getInt("current_position") + " / " + s.getInt("total_items");
        String title, phrase;
        switch (phase) {
            case "explanation": title="상황 익히기"; phrase=c.getString("explanation_ko"); break;
            case "example": title="영어 표현 듣기"; phrase=c.getString("example_en"); break;
            case "question": case "awaiting_answer": title="영어로 답해 보세요"; phrase=c.getString("question_ko"); break;
            case "evaluating": title="답변 평가"; phrase=s.isNull("evaluation_error") ? "답변을 확인하고 있어요." : "평가를 완료하지 못했어요."; break;
            case "feedback": title="학습 피드백"; phrase=s.getJSONObject("current_evaluation").getString("feedback_ko"); break;
            default: throw new JSONException("지원하지 않는 학습 단계");
        }
        page(section,title);
        TextView medallion = label(phase.equals("awaiting_answer") ? "●" : "◖))",26,MINT);
        medallion.setGravity(Gravity.CENTER);
        medallion.setBackground(round(0xff214a40,48));
        LinearLayout.LayoutParams disc = new LinearLayout.LayoutParams(dp(80),dp(80));
        disc.gravity = Gravity.CENTER_HORIZONTAL;
        disc.topMargin = dp(12);
        content.addView(medallion,disc);
        TextView prompt = label(phrase,28,Color.WHITE);
        prompt.setGravity(Gravity.CENTER);
        prompt.setPadding(dp(8),dp(16),dp(8),dp(20));
        content.addView(prompt);
        if (phase.equals("evaluating")) {
            JSONObject failure = s.optJSONObject("evaluation_error");
            if (failure == null) handler.postDelayed(() -> { if (foreground && !busy) detail(sid); },1500);
            else {
                if (failure.optBoolean("retryable")) button("평가 다시 요청",true,() -> load(
                    () -> api.request("POST","attempts/"+s.getString("pending_attempt_id")+"/retry-evaluation",
                    obj("expected_version",s.getInt("version")),id()), r -> detail(sid)));
                button("다시 답하기",false,() -> command("RESET_ANSWER",obj(),false));
            }
        } else {
            if (phase.equals("awaiting_answer")) button("영어로 답하기",true,this::listen);
            else if (phase.equals("feedback")) {
                JSONObject ev = s.getJSONObject("current_evaluation");
                if (!ev.isNull("suggested_answer_en")) {
                    String answer = ev.getString("suggested_answer_en");
                    line(answer);
                    button("추천 표현 듣기",false,() -> speak(answer,Locale.US));
                }
                for (String action : new String[]{"next","retry"}) button(action.equals("next")?"다음 표현":"다시 연습",action.equals("next"),
                    () -> command("ACK_FEEDBACK",obj("attempt_id",s.optString("pending_attempt_id"),"action",action),false));
            } else button("다음",true,() -> command("ADVANCE",obj("item_id",item,"from_phase",phase),false));
            Locale locale = phase.equals("example") ? Locale.US : Locale.KOREA;
            button("다시 듣기",false,() -> speak(phrase,locale));
            if (foreground && (vehicleVoice != null ? vehicleVoice.canSpeak() : voiceReady)) speak(phrase,locale);
        }
        button("오늘은 여기까지",false,() -> command("PAUSE",obj("reason","session_exit"),true));
    }

    void speak(String phrase, Locale locale) {
        if (!foreground || capturing) return;
        boolean ready = vehicleVoice != null ? vehicleVoice.canSpeak() : voiceReady && voice.setLanguage(locale) >= 0;
        if (!ready) {
            Toast.makeText(this,"해당 언어의 음성 출력을 사용할 수 없어요.",Toast.LENGTH_SHORT).show();
            return;
        }
        if (audio.requestAudioFocus(focus,AudioManager.STREAM_MUSIC,AudioManager.AUDIOFOCUS_GAIN_TRANSIENT) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) return;
        if (vehicleVoice != null) vehicleVoice.speak(phrase, locale);
        else {
            voice.setSpeechRate((float)preferences.optDouble("speech_rate",1));
            voice.speak(phrase,TextToSpeech.QUEUE_FLUSH,null,id());
        }
    }

    void notifySpeech(String message) {
        if (!isDestroyed()) Toast.makeText(this,message,Toast.LENGTH_SHORT).show();
    }

    void stopSpeech() {
        if (vehicleVoice != null) {
            capturing = false;
            recognitionSession = null;
            vehicleVoice.stop();
        }
        if (voice != null) voice.stop();
    }

    void listen() {
        if (capturing || restricted || !foreground) return;
        stopSpeech();
        audio.abandonAudioFocus(focus);
        recognitionSession = session.optString("session_id");
        if (vehicleVoice != null) {
            capturing = vehicleVoice.listen(this::recognized);
            notifySpeech(capturing ? "듣고 있어요. 영어로 답해 주세요." : "차량 음성 인식 서비스가 준비되지 않았어요.");
            return;
        }
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE,"en-US");
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        try { capturing=true; startActivityForResult(intent,7); }
        catch (ActivityNotFoundException e) {
            capturing=false;
            Toast.makeText(this,"이 기기에 영어 음성 인식 서비스가 없습니다.",Toast.LENGTH_LONG).show();
        }
    }

    @Override protected void onActivityResult(int code,int result,Intent data) {
        super.onActivityResult(code,result,data);
        if (code!=7) return;
        ArrayList<String> words = data==null ? null : data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
        recognized(result==RESULT_OK && words!=null && !words.isEmpty() ? words.get(0) : null);
    }

    void recognized(String transcript) {
        capturing=false;
        if (restricted || session==null || !session.optString("session_id").equals(recognitionSession)) return;
        if (transcript==null || transcript.trim().isEmpty()) {
            Toast.makeText(this,"답변을 듣지 못했어요. 다시 말해 주세요.",Toast.LENGTH_SHORT).show(); return;
        }
        JSONObject answer=obj("attempt_id",id(),"client_capture_id",id(),"expected_version",session.optInt("version"),
            "session_item_id",session.optString("current_item_id"),"capture_status","recognized","transcript",transcript,
            "recognition_locale","en-US","captured_at",Instant.now().toString());
        String sid=recognitionSession;
        load(() -> { api.saveAnswer("sessions/"+sid+"/attempts",answer); api.flushAnswer(); return null; }, r -> detail(sid));
    }

    void interrupt(String reason) {
        stopSpeech();
        if (busy && session != null) pendingPause = reason;
        if (!busy && !capturing && session!=null && "active".equals(session.optString("status"))) {
            handler.removeCallbacksAndMessages(null);
            command("PAUSE",obj("reason",reason),false);
        }
    }
    @Override protected void onResume() { super.onResume(); foreground=true; }
    @Override protected void onStop() {
        foreground=false;
        if (vehicleVoice!=null || !capturing) interrupt("app_background");
        audio.abandonAudioFocus(focus);
        super.onStop();
    }
    @Override public void onBackPressed() {
        if (restricted) { super.onBackPressed(); return; }
        if (busy) return;
        if (session!=null && "active".equals(session.optString("status"))) command("PAUSE",obj("reason","session_exit"),true);
        else if (api.configured() && !atHome()) home();
        else super.onBackPressed();
    }
    boolean atHome() { return sectionName.equals("홈"); }
    @Override protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        audio.abandonAudioFocus(focus);
        if (voice!=null) voice.shutdown();
        if (vehicleVoice!=null) vehicleVoice.close();
        if (driving!=null) driving.close();
        io.shutdown();
        super.onDestroy();
    }
}
