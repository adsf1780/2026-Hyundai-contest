package kr.eartalk.car;

import android.content.Context;
import android.graphics.*;
import android.graphics.drawable.GradientDrawable;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.function.Consumer;

/** Read-only presentation of the existing /progress response; null time stays unknown. */
final class LearningHistoryView extends LinearLayout {
    private static final int INK=0xff132c26, GREEN=0xff175747, MUTED=0xff789089,
            BORDER=0xffe2e9e5, MINT=0xff5dc4a5;
    private final YearMonth month;
    private final LocalDate end;
    private LocalDate weekEnd;
    private final JSONObject data;
    private final LinearLayout charts;
    private final LinearLayout weekControls;
    private final boolean compact;
    private final Map<LocalDate,JSONObject> days = new HashMap<>();

    LearningHistoryView(Context context, YearMonth month, LocalDate end, JSONObject data,
                        JSONObject previous, Consumer<YearMonth> selectMonth) throws JSONException {
        super(context);
        this.month=month; this.end=end; this.weekEnd=end; this.data=data;
        compact=getResources().getConfiguration().screenWidthDp>=700;
        setOrientation(VERTICAL);
        setPadding(0,dp(compact?6:18),0,0);
        JSONArray entries=data.getJSONArray("days");
        for(int i=0;i<entries.length();i++) {
            JSONObject day=entries.getJSONObject(i);
            days.put(LocalDate.parse(day.getString("date")),day);
        }
        LinearLayout picker=row();
        picker.setGravity(Gravity.CENTER_VERTICAL);
        picker.addView(control("‹","이전 달",true,() -> selectMonth.accept(month.minusMonths(1))));
        TextView date=text(month.getYear()+"년 "+month.getMonthValue()+"월",18,INK,true);
        date.setGravity(Gravity.CENTER);
        picker.addView(date,new LayoutParams(dp(164),dp(48)));
        picker.addView(control("›","다음 달",month.isBefore(YearMonth.now()),() -> selectMonth.accept(month.plusMonths(1))));
        weekControls=row();
        if(compact) {
            LinearLayout toolbar=row();
            toolbar.addView(picker,new LayoutParams(0,-2,1));
            toolbar.addView(weekControls,new LayoutParams(dp(260),-2));
            addView(toolbar);
        } else addView(picker);

        JSONObject totals=data.getJSONObject("totals");
        int completed=totals.getInt("completed_sessions");
        int delta=completed-previous.getJSONObject("totals").getInt("completed_sessions");
        boolean wide=getResources().getConfiguration().screenWidthDp>=700;
        LinearLayout cards=new LinearLayout(context);
        cards.setOrientation(wide ? HORIZONTAL : VERTICAL);
        addView(cards,spaced(compact?8:16));
        addCard(cards,card("완료한 수업",completed+"", "개",
                "지난달보다 "+(delta>0?"+":"")+delta+"개"),wide,0);
        addCard(cards,card("학습한 표현",totals.getInt("graded_items")+"","개","평가 완료 · 복습 포함"),wide,1);
        boolean known=!totals.isNull("active_seconds");
        addCard(cards,card("총 학습 시간",known?duration(totals.optLong("active_seconds")):"—","",
                known?"기록된 시간 기준 · 추정":"아직 측정된 시간이 없어요"),wide,2);
        TextView note=text("선택한 달 기준 · 표현 수에는 같은 표현의 반복 학습이 포함돼요.",13,MUTED,false);
        addView(note,spaced(compact?6:10));
        charts=new LinearLayout(context);
        charts.setOrientation(VERTICAL);
        LayoutParams chartLayout=compact?new LayoutParams(-1,0,1):spaced(20);
        if(compact) chartLayout.topMargin=dp(8);
        addView(charts,chartLayout);
        renderCharts();
    }

    private int dp(int n) { return Math.round(n*getResources().getDisplayMetrics().density); }
    private TextView text(String value,int size,int color,boolean bold) {
        TextView t=new TextView(getContext()); t.setText(value); t.setTextSize(size); t.setTextColor(color);
        if(bold) t.setTypeface(Typeface.DEFAULT,Typeface.BOLD);
        return t;
    }
    private LinearLayout row() { LinearLayout r=new LinearLayout(getContext()); r.setOrientation(HORIZONTAL); return r; }
    private LayoutParams spaced(int top) { LayoutParams p=new LayoutParams(-1,-2); p.topMargin=dp(top); return p; }
    private GradientDrawable background(int color,int radius) {
        GradientDrawable d=new GradientDrawable(); d.setColor(color); d.setCornerRadius(dp(radius));
        d.setStroke(dp(1),BORDER); return d;
    }
    private Button control(String value,String description,boolean enabled,Runnable action) {
        Button b=new Button(getContext()); b.setText(value); b.setTextSize(22); b.setTextColor(GREEN);
        b.setBackground(background(Color.WHITE,12)); b.setContentDescription(description);
        b.setEnabled(enabled); b.setAlpha(enabled?1f:.35f); b.setOnClickListener(v -> action.run());
        b.setLayoutParams(new LayoutParams(dp(48),dp(48))); return b;
    }
    private LinearLayout panel() {
        LinearLayout p=new LinearLayout(getContext()); p.setOrientation(VERTICAL);
        p.setPadding(dp(compact?16:22),dp(compact?12:20),dp(compact?16:22),dp(compact?12:20)); p.setBackground(background(Color.WHITE,24)); return p;
    }
    private LinearLayout card(String title,String value,String unit,String detail) {
        LinearLayout p=panel(); p.addView(text(title,14,MUTED,false));
        LinearLayout number=row(); number.setGravity(Gravity.BOTTOM);
        number.addView(text(value,compact?28:32,INK,true));
        TextView suffix=text(unit,15,INK,false); suffix.setPadding(dp(4),0,0,dp(5)); number.addView(suffix);
        p.addView(number,spaced(compact?4:14)); p.addView(text(detail,12,GREEN,false),spaced(compact?4:10)); return p;
    }
    private void addCard(LinearLayout cards,View card,boolean wide,int index) {
        LayoutParams p=new LayoutParams(wide?0:-1,-2,wide?1:0);
        if(wide && index>0) p.leftMargin=dp(12);
        if(!wide && index>0) p.topMargin=dp(10);
        cards.addView(card,p);
    }
    static String duration(long seconds) {
        if(seconds<60) return seconds+"초";
        long minutes=seconds/60;
        return minutes<60 ? minutes+"분" : minutes/60+"시간 "+minutes%60+"분";
    }
    private void renderCharts() {
        charts.removeAllViews();
        weekControls.removeAllViews();
        LocalDate start=weekEnd.minusDays(6);
        if(start.isBefore(month.atDay(1))) start=month.atDay(1);
        final LocalDate weekStart=start;
        LinearLayout navigation=row(); navigation.setGravity(Gravity.CENTER_VERTICAL);
        navigation.addView(control("‹","이전 주",start.isAfter(month.atDay(1)),() -> {
            weekEnd=weekStart.minusDays(1); renderCharts();
        }));
        TextView range=text(start.format(DateTimeFormatter.ofPattern("M.d"))+" – "+
                weekEnd.format(DateTimeFormatter.ofPattern("M.d")),16,INK,true);
        range.setGravity(Gravity.CENTER);
        navigation.addView(range,new LayoutParams(0,dp(48),1));
        navigation.addView(control("›","다음 주",weekEnd.isBefore(end),() -> {
            weekEnd=weekEnd.plusDays(7).isAfter(end)?end:weekEnd.plusDays(7); renderCharts();
        }));
        if(compact) weekControls.addView(navigation,new LayoutParams(-1,-2));
        else charts.addView(navigation);
        LinearLayout plots=new LinearLayout(getContext());
        boolean wide=compact;
        plots.setOrientation(wide?HORIZONTAL:VERTICAL);
        charts.addView(plots,compact?new LayoutParams(-1,-1):spaced(12));
        addCard(plots,chartPanel("주간 활동","학습 시간",start,true),wide,0);
        addCard(plots,chartPanel("학습한 표현","일별 학습 현황",start,false),wide,1);
        if(compact) for(int i=0;i<plots.getChildCount();i++) plots.getChildAt(i).getLayoutParams().height=LayoutParams.MATCH_PARENT;
    }
    private LinearLayout chartPanel(String eyebrow,String heading,LocalDate start,boolean time) {
        LinearLayout p=panel(); p.addView(text(eyebrow,12,MUTED,false));
        p.addView(text(heading,compact?20:22,INK,true),spaced(compact?2:6));
        ArrayList<JSONObject> week=new ArrayList<>(); long total=0; int known=0;
        for(LocalDate date=start;!date.isAfter(weekEnd);date=date.plusDays(1)) {
            JSONObject day=days.get(date);
            if(day==null) continue;
            week.add(day);
            String key=time?"active_seconds":"graded_items";
            if(!day.isNull(key)) { total+=day.optLong(key); known++; }
        }
        p.addView(text(time?(known==0?"측정된 시간 없음":"선택 주간 "+duration(total)):
                "선택 주간 "+total+"개",14,GREEN,true),spaced(compact?2:8));
        p.addView(new Bars(getContext(),week,time),compact?new LayoutParams(-1,0,1):new LayoutParams(-1,dp(210)));
        p.addView(text(time?"— 시간 미측정 · 음성 재생·답변 시간 기준 (추정)":"평가를 마친 표현 수 · 반복 학습 포함",12,MUTED,false));
        if(!time && total==0) p.addView(text("아직 학습 기록이 없어요.",14,GREEN,false),spaced(compact?2:10));
        return p;
    }

    private final class Bars extends View {
        private final ArrayList<JSONObject> week;
        private final boolean time;
        private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
        Bars(Context c,ArrayList<JSONObject> week,boolean time) {
            super(c); this.week=week; this.time=time;
            StringBuilder description=new StringBuilder(time?"일별 학습 시간. ":"일별 학습 표현. ");
            for(JSONObject day:week) description.append(day.optString("date")).append(": ")
                    .append(time?(day.isNull("active_seconds")?"미측정":duration(day.optLong("active_seconds"))):day.optInt("graded_items")+"개").append(". ");
            setContentDescription(description); setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
        }
        @Override protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            if(week.isEmpty()) return;
            float top=dp(34),bottom=getHeight()-dp(48),left=dp(4),width=getWidth()-dp(8);
            double max=1;
            for(JSONObject day:week) max=Math.max(max,value(day));
            paint.setStrokeWidth(dp(1));
            for(int i=0;i<3;i++) {
                paint.setColor(BORDER); float y=top+(bottom-top)*i/2;
                canvas.drawLine(left,y,getWidth()-dp(4),y,paint);
            }
            float slot=width/week.size(),bar=Math.min(dp(36),slot*.55f);
            paint.setTextAlign(Paint.Align.CENTER);
            float scaled=getResources().getDisplayMetrics().scaledDensity;
            for(int i=0;i<week.size();i++) {
                JSONObject day=week.get(i); float x=left+slot*(i+.5f);
                boolean unknown=time && day.isNull("active_seconds"); double value=value(day);
                paint.setColor(0xfff0f4f1);
                canvas.drawRoundRect(x-bar/2,top,x+bar/2,bottom,dp(9),dp(9),paint);
                if(!unknown && value>0) {
                    paint.setColor(time?GREEN:MINT);
                    float height=(float)((bottom-top)*value/max);
                    canvas.drawRoundRect(x-bar/2,bottom-height,x+bar/2,bottom,dp(8),dp(8),paint);
                }
                paint.setColor(INK); paint.setTextSize(11*scaled);
                String amount=unknown?"—":time?(value<60?(long)value+"초":String.format(Locale.KOREA,"%.1f분",value/60)):(long)value+"";
                canvas.drawText(amount,x,top-dp(12),paint);
                LocalDate date=LocalDate.parse(day.optString("date"));
                paint.setColor(MUTED); paint.setTextSize(12*scaled);
                canvas.drawText(new String[]{"월","화","수","목","금","토","일"}[date.getDayOfWeek().getValue()-1],x,bottom+dp(22),paint);
                paint.setTextSize(10*scaled);
                canvas.drawText(date.getMonthValue()+"/"+date.getDayOfMonth(),x,bottom+dp(38),paint);
            }
        }
        private double value(JSONObject day) { return day.optDouble(time?"active_seconds":"graded_items",0); }
    }
}
