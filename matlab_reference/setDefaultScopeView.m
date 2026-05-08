function [] = setDefaultScopeView(scope,amplitude1_new,currentMonScale)
%% Variables
currentMonScale_V_uA = currentMonScale * 1e-3;% mV/uA to V/uA
amplitude1_mag = abs(amplitude1_new);
amplitude1_sign = sign(amplitude1_new);
if amplitude1_mag < 10
    triggerLevel = amplitude1_new * currentMonScale_V_uA + 4.5 * currentMonScale_V_uA * amplitude1_sign;
else
    triggerLevel = amplitude1_new * currentMonScale_V_uA * 0.6;
end

%% Function
% Horizontal setup
isHorizScaleGood = false;
while ~isHorizScaleGood
    fprintf(scope,'HORizontal:MAIn:SCAle 100E-6');    % 100 us/div horizontal scale
    fprintf(scope,'HORizontal:MAIN:SCAle?');
    horizScale = str2num(fscanf(scope));
    if horizScale == 100e-6
        isHorizScaleGood = true;
    end
end
fprintf('Setting horizontal scale...');
fprintf('%.2e \n',horizScale);

fprintf('Setting horizontal position...');
isHorizPosGood = false;
while ~isHorizPosGood
    fprintf(scope,'HORizontal:MAIN:POSition 300E-6'); % 300 us horizontal position
    fprintf(scope,'HORizontal:MAIN:POSition?');
    horizPos = str2num(fscanf(scope));
    if horizPos == 300e-6
        isHorizPosGood = true;
        fprintf('%.2e \n',horizPos);
    end
end

% voltage scale
fprintf('Setting CH1 scale...');
fprintf(scope,'CH1:SCAle 500E-3');  % 500 mV/div vertical scale for voltage
fprintf('500 mV\n');
% current scale
fprintf('Setting CH2 scale...');
% fprintf(scope,'CH2:SCAle 100-3');  % 100 mV/div vertical scale for current
source = 'CH2';
scale = 100e-3;
scale_use = sprintf('%s:SCAle %.g',source,scale);
fprintf(scope,scale_use);
fprintf('100 mA\n');

% Trigger level
fprintf('Setting trigger level...');
triggerLevel_text = sprintf('TRIGger:MAIn:LEVel %f',triggerLevel);
fprintf(scope,triggerLevel_text);
% fprintf('Trigger Level: %.2e A\n',triggerLevel);
fprintf('%.2f A\n',triggerLevel);