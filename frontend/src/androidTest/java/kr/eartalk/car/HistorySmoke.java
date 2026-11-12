package kr.eartalk.car;

import android.app.*;
import android.content.*;
import android.graphics.*;
import android.os.Bundle;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.io.FileOutputStream;
import java.time.*;
import java.util.concurrent.atomic.AtomicReference;

/** Isolated rendering checks: no server, account changes, or learning writes. */
final class HistorySmoke {
    static void run(Instrumentation test) {
        Bundle report=new Bundle(); Activity activity=null; int result=Activity.RESULT_CANCELED;
        try {
            Intent intent=new Intent(Intent.ACTION_MAIN).setClassName(test.getTargetContext(),"kr.eartalk.car.MainActivity")
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity=test.startActivitySync(intent);
            MainActivity main=(MainActivity)activity;
            AtomicReference<Throwable> failure=new AtomicReference<>();
            test.runOnMainSync(() -> {
                try {
                    main.page("학습 기록","조금씩 쌓인 변화가 보여요");
                    LinearLayout content=(LinearLayout)((TextView)find(main.getWindow().getDecorView(),
                            "조금씩 쌓인 변화가 보여요")).getParent();
                    YearMonth month=YearMonth.now(); LocalDate end=month.atEndOfMonth();
                    AtomicReference<YearMonth> selected=new AtomicReference<>();
                    LearningHistoryView empty=new LearningHistoryView(main,month,end,fixture(month,false),
                            fixture(month.minusMonths(1),false),selected::set);
                    content.addView(empty,new LinearLayout.LayoutParams(-1,0,1));
                    require(find(empty,"아직 측정된 시간이 없어요")!=null,"null time must remain unknown");
                    require(find(empty,"측정된 시간 없음")!=null,"null time chart must remain unknown");
                    require(!findDescription(empty,"다음 달").isEnabled(),"future month must be disabled");
                    findDescription(empty,"이전 달").performClick();
                    require(month.minusMonths(1).equals(selected.get()),"previous month callback");
                    Bitmap emptyPreview=draw(content,1012,520);
                    assertFits(content);
                    emptyPreview.recycle();
                    content.removeView(empty);
                    LearningHistoryView populated=new LearningHistoryView(main,month,end,fixture(month,true),
                            fixture(month.minusMonths(1),false),selected::set);
                    content.addView(populated,new LinearLayout.LayoutParams(-1,0,1));
                    require(find(populated,"평가 완료 · 복습 포함")!=null,"expression semantics");
                    findDescription(populated,"이전 주").performClick();
                    require(findDescription(populated,"다음 주").isEnabled(),"week navigation");
                    findDescription(populated,"다음 주").performClick();
                    require(!findDescription(populated,"다음 주").isEnabled(),"last week boundary");
                    Bitmap bitmap=draw(content,1012,520);
                    assertFits(content);
                    try(FileOutputStream out=new FileOutputStream(main.getExternalFilesDir(null)+"/history-preview.png")) {
                        bitmap.compress(Bitmap.CompressFormat.PNG,100,out);
                    }
                    bitmap.recycle();
                } catch(Throwable e) { failure.set(e); }
            });
            if(failure.get()!=null) throw new AssertionError(failure.get());
            report.putString("stream","PASS: null/zero/mixed time, month/week navigation, all cards and graphs fit a 1012x520 viewport without scrolling");
            result=Activity.RESULT_OK;
        } catch(Throwable e) { report.putString("stream","FAIL: "+e); }
        finally { if(activity!=null) { Activity closing=activity; test.runOnMainSync(closing::finish); } }
        test.finish(result,report);
    }
    static Bitmap draw(View view,int width,int height) {
        view.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(height,View.MeasureSpec.EXACTLY));
        view.layout(0,0,width,view.getMeasuredHeight());
        Bitmap bitmap=Bitmap.createBitmap(width,view.getMeasuredHeight(),Bitmap.Config.ARGB_8888);
        Canvas canvas=new Canvas(bitmap); canvas.drawColor(0xfff3f6f4); view.draw(canvas); return bitmap;
    }
    private static JSONObject fixture(YearMonth month,boolean populated) throws JSONException {
        JSONArray days=new JSONArray(); long seconds=0; int expressions=0;
        for(int i=1;i<=month.lengthOfMonth();i++) {
            Object time=!populated || i%7==0?JSONObject.NULL:(i%4)*120;
            int count=populated?i%5:0; expressions+=count;
            if(time instanceof Integer) seconds+=(Integer)time;
            days.put(MainActivity.obj("date",month.atDay(i).toString(),"active_seconds",time,
                    "graded_items",count,"completed_sessions",populated && i%3==0?1:0));
        }
        return MainActivity.obj("days",days,"totals",MainActivity.obj("completed_sessions",populated?8:0,
                "graded_items",expressions,"active_seconds",populated?seconds:JSONObject.NULL));
    }
    static View find(View root,String text) {
        if(root instanceof TextView && text.contentEquals(((TextView)root).getText())) return root;
        if(root instanceof ViewGroup) for(int i=0;i<((ViewGroup)root).getChildCount();i++) {
            View match=find(((ViewGroup)root).getChildAt(i),text); if(match!=null) return match;
        }
        return null;
    }
    private static View findDescription(View root,String text) {
        if(text.contentEquals(root.getContentDescription()==null?"":root.getContentDescription())) return root;
        if(root instanceof ViewGroup) for(int i=0;i<((ViewGroup)root).getChildCount();i++) {
            View match=findDescription(((ViewGroup)root).getChildAt(i),text); if(match!=null) return match;
        }
        return null;
    }
    static void assertFits(View root) {
        require(!(root instanceof ScrollView),"dashboard must not scroll");
        if(root instanceof ViewGroup) {
            ViewGroup group=(ViewGroup)root;
            for(int i=0;i<group.getChildCount();i++) {
                View child=group.getChildAt(i);
                require(child.getBottom()<=group.getHeight()-group.getPaddingBottom()+1,"content exceeds viewport: "+child);
                require(child.getRight()<=group.getWidth()-group.getPaddingRight()+1,"content exceeds width: "+child);
                assertFits(child);
            }
        } else if(root.getClass().getSimpleName().equals("Bars")) require(root.getHeight()>=80,"graph must retain readable height");
    }
    private static void require(boolean condition,String message) { if(!condition) throw new AssertionError(message); }
}
