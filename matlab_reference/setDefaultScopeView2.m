function [] = setDefaultScopeView2(scope,amplitude1)
%% Horizontal setup
fprintf('Setting horizontal scale...');
isHorizScaleGood = false;
while ~isHorizScaleGood
    fprintf(scope,'HORizontal:MAIn:SCAle 100E-6');    % 100 us/div horizontal scale
    fprintf(scope,'HORizontal:MAIN:SCAle?');
    horizScale = str2num(fscanf(scope));
    if horizScale == 100e-6
        isHorizScaleGood = true;
        fprintf('%.2e \n',horizScale);
    end
end
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

%% Voltage Scale
fprintf('Setting CH1 scale...');
fprintf(scope,'CH1:SCAle 200e-3');  % 200 mV/div vertical scale for voltage
fprintf('1 V\n');
% current scale
fprintf('Setting CH2 scale...');
% fprintf(scope,'CH2:SCAle 100-3');  % 100 mV/div vertical scale for current
source = 'CH2';
scale = 50e-3;
scale_use = sprintf('%s:SCAle %.g',source,scale);
fprintf(scope,scale_use);
fprintf('100 mA\n');

%% Trigger Level
% fprintf('Setting trigger level...');
% triggerLevel_text = sprintf('TRIGger:MAIn:LEVel %f',triggerLevel);
% fprintf(scope,triggerLevel_text);
% % fprintf('Trigger Level: %.2e A\n',triggerLevel);
% fprintf('%.2f A\n',triggerLevel);
adjustTriggerLevel(scope,amplitude1);