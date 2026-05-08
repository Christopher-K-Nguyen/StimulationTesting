function [wiperPosition,outputVoltage] = getWiper(voltage)
%% Constants
NUM_OF_STEPS = 128;     % 7-bit steps
TOTAL_RESISTANCE = 10e3;% 10-kOhm
V_IN = 3.3;               % 3.3V at RH (high terminal)

%% Wiper Position
voltageDivider = TOTAL_RESISTANCE * (voltage / (V_IN - voltage));       % Calculate R2 using voltage divider equation
wiperPosition = voltageDivider * (NUM_OF_STEPS - 1) / TOTAL_RESISTANCE; % Calculate step position
wiperPosition = fix(wiperPosition);

%% Rounded Output Voltage
lowerResistance = wiperPosition / (NUM_OF_STEPS - 1) * TOTAL_RESISTANCE;
outputVoltage = lowerResistance / TOTAL_RESISTANCE * V_IN;

end