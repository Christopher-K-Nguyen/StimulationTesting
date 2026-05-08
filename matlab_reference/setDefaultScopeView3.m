function [] = setDefaultScopeView3(File,amplitude1)
%% Constants
TIME_SCALE = [250 100 50 25 10 5 2.5];

%% Scope
stimType = File.Parameters.Type;
isMultiTest = contains2(stimType,'MULTI');
setScopeStatus(File,'open');
oscilloscope = File.Oscilloscope.Object;
trigSource = File.Oscilloscope.Trigger.Source;
if contains2(trigSource,'EXT')
    try
        digitalDelay = File.Stimulator.DigitalDelay;
    catch
        digitalDelay = 1.5;
    end
else
    digitalDelay = 0;
end
phaseWidth1 = File.Parameters.PhaseWidth1;
interphaseDelay = File.Parameters.InterphaseDelay;
phaseWidth2 = File.Parameters.PhaseWidth2;
pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;

%% Horizontal setup
fprintf('Setting horizontal position...');
startTime = tic;
% horizScale_check = [];
% horizScale = 1e-6;
% horizScale_use = sprintf('HORizontal:MAIn:SCAle %.3e',horizScale);
% while ~isequal(horizScale_check,horizScale)
%     fprintf(oscilloscope,horizScale_use);
%     fprintf(oscilloscope,'HORizontal:MAIN:SCAle?');
%     horizScale_raw = fgetl2(oscilloscope);
%     horizScale_check = str2double(horizScale_raw);
% end
numOfTimeScale = length(TIME_SCALE);
for timeScale_idx = 1:numOfTimeScale
    timeScale = TIME_SCALE(timeScale_idx);
    timeWidth = timeScale * 10;
    fract = pulseWidth / timeWidth;
    if fract >= 0.4
        break;
    end
end
if fract >= 0.6
    shift = 4 * timeScale;
elseif fract >= 0.4
    shift = 3 * timeScale;
else
    shift = 3 * timeScale;
end
if contains(trigSource,'EXT')
%     if timeScale > 10
%         digitalDelay = round(digitalDelay);
%     end
    shift_s = (shift + digitalDelay) * 1e-6;
else
    shift_s = shift * 1e-6;
end
% horizPos = round(shift_s,3,'significant');
horizPos = shift_s;
horizPos_check = [];
horizPos_use = sprintf('HORizontal:MAIN:POSition %.3e',horizPos);
while ~isequal(horizPos_check,horizPos)
    fprintf(oscilloscope,horizPos_use); % horizontal position
    fprintf(oscilloscope,'HORizontal:MAIN:POSition?');
    horizPos_raw = fgetl2(oscilloscope);
    horizPos_check = str2double(horizPos_raw);
end
[endTime,unit] = getEndTime(startTime);
fprintf('%.3e s (%.2f %s)\n',horizPos,endTime,unit);

fprintf('Setting horizontal scale...');
startTime = tic;
timeScale_s = timeScale * 1e-6;
horizScale = round(timeScale_s,3,'significant');
horizScale_check = [];
horizScale_use = sprintf('HORizontal:MAIn:SCAle %.2e',horizScale);
while ~isequal(horizScale_check,horizScale)
    fprintf(oscilloscope,horizScale_use);
    fprintf(oscilloscope,'HORizontal:MAIN:SCAle?');
    horizScale_raw = fgetl2(oscilloscope);
    horizScale_check = str2double(horizScale_raw);
end
[endTime,unit] = getEndTime(startTime);
fprintf('%.2e s (%.2f %s)\n',horizScale,endTime,unit);

%% Cursor Setup
fprintf('Opening cursors...');
startTime = tic;
fprintf(oscilloscope,'CURSor:SELect:SOUrce CH1');
fprintf(oscilloscope,'CURSor:FUNCtion VBArs');
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);
vertScale1 = 200e-3;
if phaseWidth1 >= 100
    vertScale1 = 200e-3;
    cursor_shift = 2e-6;
    depolTime = 12;
elseif interphaseDelay > 10
    depolTime = 6.5;
% elseif phaseWidth1 >= 25
%     depolTime = ceil(phaseWidth1 * 0.06);
elseif interphaseDelay == 0
    depolTime = 0;
    vertScale1 = 200e-6;
else
    subjectSelect = File.Subject;
    if contains2(subjectSelect,'A')
        vertScale1 = 1;
    else
        vertScale1 = 500e-3;
    end
    cursor_shift = 3e-6;
    depolTime = interphaseDelay / 2;
end
% cursor 1
fprintf('Setting Cursor 1 position...');
startTime = tic;
drivingVoltage_time = (phaseWidth1) * 1e-6;
drivingVoltage_time_cursor = drivingVoltage_time;
fprintf(oscilloscope,'CURSor:FUNCtion VBArs');
fprintf(oscilloscope,'CURSor:SELect:SOUrce CH1');
verticalBar1 = sprintf('CURSor:VBArs:POSITION1 %.2e',drivingVoltage_time_cursor);
fprintf(oscilloscope,verticalBar1);
[endTime,unit] = getEndTime(startTime);
fprintf('%.2e s (%.2f %s)\n',drivingVoltage_time_cursor,endTime,unit);
% cursor 2
fprintf('Setting Cursor 2 position...');
startTime = tic;
potentialExcursion_time = drivingVoltage_time + (depolTime) * 1e-6;
verticalBar2 = sprintf('CURSor:VBArs:POSITION2 %.2e',potentialExcursion_time);
fprintf(oscilloscope,verticalBar2);
[endTime,unit] = getEndTime(startTime);
fprintf('%.2e s (%.2f %s)\n',potentialExcursion_time,endTime,unit);

%% Voltage Scale
if isMultiTest
    numOfChannels = 3;
else
    numOfChannels = 1;
end
scopeChannel_cell = {'CH1','CH3','CH4'};
for channel_idx = 1:numOfChannels
    scopeChannel = scopeChannel_cell{channel_idx};
    fprintf('Setting %s scale...',scopeChannel);
    startTime = tic;
    vertScale1_check = 0;
    vertScale1_use = sprintf('%s:SCAle %.2e',scopeChannel,vertScale1);
    vertScale1_query = sprintf('%s:SCAle?',scopeChannel);
    while ~isequal(vertScale1_check,vertScale1)
        fprintf(oscilloscope,vertScale1_use);  % 200 mV/div vertical scale for voltage
        fprintf(oscilloscope,vertScale1_query);
        vertScale1_raw = fgetl2(oscilloscope);
        vertScale1_check = str2double(vertScale1_raw);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2e V (%.2f %s)\n',vertScale1,endTime,unit);
end

%% Current Scale
setCurrentScale(File,amplitude1); 

%% Trigger Level
setTriggerLevel2(File);

end