package kr.eartalk.car;

import android.car.Car;
import android.car.drivingstate.CarUxRestrictionsManager;
import android.car.drivingstate.CarUxRestrictions;
import android.content.Context;
import java.util.function.Consumer;

final class DrivingState {
    private final Car car;
    private final CarUxRestrictionsManager manager;
    DrivingState(Context context, Consumer<Boolean> restricted) {
        car = Car.createCar(context);
        manager = (CarUxRestrictionsManager) car.getCarManager(Car.CAR_UX_RESTRICTION_SERVICE);
        CarUxRestrictionsManager.OnUxRestrictionsChangedListener listener =
                value -> restricted.accept(value == null || value.isRequiresDistractionOptimization()
                        || value.getActiveRestrictions() != CarUxRestrictions.UX_RESTRICTIONS_BASELINE);
        manager.registerListener(listener);
        listener.onUxRestrictionsChanged(manager.getCurrentCarUxRestrictions());
    }
    void close() {
        try { manager.unregisterListener(); } finally { car.disconnect(); }
    }
}
