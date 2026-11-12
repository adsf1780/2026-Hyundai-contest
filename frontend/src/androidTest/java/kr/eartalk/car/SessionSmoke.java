package kr.eartalk.car;

import android.app.Instrumentation;
import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.*;
import android.widget.*;
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
    @Override public void onStart() {
        Bundle report=new Bundle();
        try {
            new Api(getTargetContext()).configure(args.getString("origin"),args.getString("token"));
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
            report.putString("stream","PASS: home, lesson, advance, pause, resume");
            finish(Activity.RESULT_OK,report);
        } catch (Throwable failure) {
            report.putString("stream","FAIL: "+failure);
            finish(Activity.RESULT_CANCELED,report);
        }
    }
}
