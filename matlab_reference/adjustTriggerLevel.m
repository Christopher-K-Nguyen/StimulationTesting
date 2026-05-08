function adjustTriggerLevel(scope,amplitude1)
%% Constants
currentMonScale_V_uA = 1e-3;% mV/uA to V/uA

%% Variables
amplitude1_mag = abs(amplitude1);
amplitude1_sign = sign(amplitude1);
if amplitude1_mag < 20  % current amplitude too small
    triggerLevel = (amplitude1_mag  + 4.5) * amplitude1_sign * currentMonScale_V_uA;
else
    triggerLevel = amplitude1 * currentMonScale_V_uA * 0.5;
end

%% Function
% Adjust trigger level
fprintf('Setting trigger Level...');
triggerLevel_use = sprintf('TRIGger:MAIn:LEVel %f',triggerLevel);
fprintf(scope,'TRIGger:MAIn:EDGe:SOUrce CH2');          % trigger source on current
fprintf(scope,triggerLevel_use);
fprintf('%.2e A\n',triggerLevel);

end