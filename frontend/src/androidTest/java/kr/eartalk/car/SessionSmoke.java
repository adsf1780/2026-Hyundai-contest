package kr.eartalk.car;

import android.app.Instrumentation;
import android.app.Activity;
import android.content.Intent;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.*;
import android.widget.*;
import android.view.accessibility.AccessibilityNodeInfo;
import org.json.JSONObject;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

/** Run on an isolated emulator against a seeded test account without an active session. */
public class SessionSmoke extends Instrumentation {
    private Bundle args;
    private Activity activity;

    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        args = arguments;
        start();
    }

    private TextView find(View view,String text) {
        if (view instanceof TextView && text.contentEquals(((TextView)view).getText())) return (TextView)view;
        if (view instanceof ViewGroup) {
            ViewGroup group=(ViewGroup)view;
            for (int i=0;i<group.getChildCount();i++) {
                TextView match=find(group.getChildAt(i),text);
                if (match!=null) return match;
            }
        }
        return null;
    }
    private void press(String text) {
        long end=SystemClock.elapsedRealtime()+20000;
        while (SystemClock.elapsedRealtime()<end) {
            AtomicReference<Boolean> clicked=new AtomicReference<>(false);
            runOnMainSync(() -> {
                MainActivity main=(MainActivity)activity;
                if (main.isFinishing() || main.isLoading()) return;
                View view=find(main.getWindow().getDecorView(),text);
                if (view!=null) {
                    while (!view.isClickable() && view.getParent() instanceof View) view=(View)view.getParent();
                    if (view.isClickable()) clicked.set(view.performClick());
                }
            });
            if (clicked.get()) { SystemClock.sleep(350); return; }
            SystemClock.sleep(150);
        }
        throw new AssertionError("버튼을 찾지 못했습니다. "+text);
    }
    private void pressDialog(String text) {
        long end = SystemClock.elapsedRealtime() + 10000;
        while (SystemClock.elapsedRealtime() < end) {
            AccessibilityNodeInfo root = getUiAutomation().getRootInActiveWindow();
            if (root != null) {
                for (AccessibilityNodeInfo node : root.findAccessibilityNodeInfosByText(text)) {
                    if (text.contentEquals(node.getText()) && node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                        SystemClock.sleep(350);
                        return;
                    }
                }
            }
            SystemClock.sleep(150);
        }
        throw new AssertionError("선택창 버튼을 찾지 못했습니다. " + text);
    }
    @Override public void onStart() {
        Bundle report=new Bundle();
        SharedPreferences prefs = getTargetContext().getSharedPreferences("eartalk",Context.MODE_PRIVATE);
        Map<String, ?> original = prefs.getAll();
        int outcome = Activity.RESULT_CANCELED;
        try {
            Api api = new Api(getTargetContext());
            if (api.hasPendingAnswer()) throw new AssertionError("미전송 답변이 있는 기기에서는 테스트를 실행하지 않습니다.");
            api.configure(args.getString("origin"),args.getString("token"));
            Intent intent=new Intent(Intent.ACTION_MAIN).setClassName(getTargetContext(),"kr.eartalk.car.MainActivity")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity=startActivitySync(intent);
            press("카페에서 주문하기");
            press("다음");
            press("오늘은 여기까지");
            press("▶   이어서 학습");
            press("이어서 학습");
            press("다음");
            press("오늘은 여기까지");
            press("전체 수업");
            JSONObject previous = api.get("home").getJSONObject("active_session");
            String oldId = previous.getString("session_id");
            press("처음 만난 사람에게");
            pressDialog("취소");
            if (!oldId.equals(api.get("home").getJSONObject("active_session").getString("session_id")))
                throw new AssertionError("취소 시 기존 수업이 변경되었습니다.");
            press("처음 만난 사람에게");
            pressDialog("이어서 학습");
            press("이어서 학습");
            press("오늘은 여기까지");
            press("전체 수업");
            press("처음 만난 사람에게");
            pressDialog("종료하고 시작");
            press("다음");
            JSONObject current = api.get("home").getJSONObject("active_session");
            if (oldId.equals(current.getString("session_id")) ||
                    !"abandoned".equals(api.get("sessions/" + oldId).getString("status")))
                throw new AssertionError("기존 수업 종료 후 새 수업을 생성하지 못했습니다.");
            JSONObject item = api.get("sessions/" + current.getString("session_id"))
                    .getJSONObject("current_item").getJSONObject("content");
            if (!"My name is Minji.".equals(item.getString("example_en")))
                throw new AssertionError("선택한 수업의 콘텐츠가 아닙니다.");
            press("오늘은 여기까지");
            report.putString("stream","PASS: start, pause, resume, cancel switch, continue old lesson, switch to selected lesson");
            outcome = Activity.RESULT_OK;
        } catch (Throwable failure) {
            report.putString("stream","FAIL: "+failure);
        } finally {
            if (activity != null) runOnMainSync(() -> activity.finish());
            SharedPreferences.Editor restore = prefs.edit().clear();
            for (Map.Entry<String, ?> entry : original.entrySet()) restore.putString(entry.getKey(),(String)entry.getValue());
            restore.commit();
        }
        finish(outcome,report);
    }
}
