function [] = defaultScopeView(scope,voltageMonScale,currentMonScale,amplitude1_mag)
%% Constants
CURRENT_LIMIT_80 = 80;
MILLI_TO_N = 1e-3;
TRIGGER_LEVEL_SCALE = 9;

%% Variables
currentMonScale_V_uA = currentMonScale * MILLI_TO_N;% mV/uA to V/uA
triggerLevel = amplitude1_mag * currentMonScale_V_uA * TRIGGER_LEVEL_SCALE;

%% Function
% voltage scale
fprintf(scope,'CH1:POSition 0');
if voltageMonScale < 1
    fprintf(scope,'CH1:SCAle 50E-3');	% 100 mV/div vertical scale for voltage
else
    if amplitude1_mag < CURRENT_LIMIT_80
        fprintf(scope,'CH1:SCAle 50E-3');	% 200 mV/div vertical scale for voltage
    else
        fprintf(scope,'CH1:SCAle 200E-3');	% 200 mV/div vertical scale for voltage
    end
end
% current scale
fprintf(scope,'CH2:POSition 0');
if currentMonScale > 1
     if amplitude1_mag < CURRENT_LIMIT_80
         fprintf(scope,'CH2:SCAle 50E-3');	% 50 mV/div vertical scale for current
     else
         fprintf(scope,'CH2:SCAle 100-3');	% 100 mV/div vertical scale for current
     end
else
    if amplitude1_mag < CURRENT_LIMIT_80
         fprintf(scope,'CH2:SCAle 20E-3');	% 20 mV/div vertical scale for current
    else
         fprintf(scope,'CH2:SCAle 50E-3');	% 50 mV/div vertical scale for current
     end
end

% Trigger level
triggerLevel_text = sprintf('TRIGger:MAIn:LEVel %f',triggerLevel);
fprintf(scope,triggerLevel_text);