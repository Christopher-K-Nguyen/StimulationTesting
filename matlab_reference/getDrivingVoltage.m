function [drivingVoltage,drivingVoltage_idx] = getDrivingVoltage(voltage,endPhase_idx,sign)
%% Constants
DRIVING_CHECK = 0.005;

%% Variables
isDrivingFound = false;

%% Function
drivingVoltage_idx = endPhase_idx - 10;
shiftNum = 0;
while ~isDrivingFound
    drivingVoltage = voltage(drivingVoltage_idx);
    drivingVoltage_before = voltage(drivingVoltage_idx-1);
    switch sign
        case -1
            if drivingVoltage_before - drivingVoltage < DRIVING_CHECK
                shiftNum = shiftNum + 1;
                drivingVoltage_idx = endPhase_idx - 10 - shiftNum;
            else
                isDrivingFound = true;
            end
        case 1

            if drivingVoltage_before - drivingVoltage > DRIVING_CHECK
                shiftNum = shiftNum + 1;
                drivingVoltage_idx = endPhase_idx - 10 - shiftNum;
            else
                isDrivingFound = true;
            end
    end
end

end