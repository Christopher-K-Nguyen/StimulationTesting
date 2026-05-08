function isQuit = setPattern(File,channelStim_arr,pattern,varargin)
%% Variables
isQuit = false;
try
    if ~isempty(varargin)
        channelReturn_arr = varargin{1};
    else
        channelReturn_arr = [];
    end
catch
    channelReturn_arr = [];
end
numOfReturns = length(channelReturn_arr);
% numOfReturns = 0;

% Experiment
expType = File.Test.Experiment;
isPulsing = contains2(expType,{'LP','SP'});
isTriphasic = contains2(expType,'TV');
isAlwaysArb = false;

% Channel
numOfChannelStim = length(channelStim_arr);
numOfChannels = File.Parameters.NumberOfChannels;
plexonChannel_arr = File.Parameters.Channels.Plexon;
plexoStim_arr = plexonChannel_arr(channelStim_arr);
zeroChannel_arr = 1:numOfChannels;
if numOfReturns > 0
    plexonReturn_arr = zeros(1,numOfReturns);
    for return_idx = 1:numOfReturns
        channelReturn = channelReturn_arr(return_idx);
        plexonReturn_arr(return_idx) = plexonChannel_arr(channelReturn);
    end
    exclude_idx = [plexoStim_arr plexonReturn_arr];
else
    exclude_idx = plexoStim_arr;
end
zeroChannel_arr(exclude_idx) = [];
numOfZeroChannels = length(zeroChannel_arr);

% Index
% channel_arr = [File.Data(:).ActiveChannel];
% groupNum = length(channel_arr);
% capture_arr = [File.Data(groupNum).Capture(:).Index];
% numOfCaptures = length(capture_arr);
% captureNum = capture_arr(numOfCaptures);
% if isempty(captureNum)
%     captureNum = 1;
% end

% Pattern
% amplitude1 = pattern.A1;
% amplitude2 = pattern.A2;
% phaseWidth1 = pattern.W1;
% phaseWidth2 = pattern.W2;
if isTriphasic
    % amplitude3 = pattern.A3;
    phaseWidth3 = pattern.W3;
end
% interphaseDelay = pattern.Delay;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;
isArb = hasDischargeDelay || isTriphasic;
% isDischargeOn = File.Stimulator.Discharge;

% Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isCG = contains2(configID,'CG');
% isAP = contains2(configID,'AP');

%% Reinitialize Stimulator
% isOn = File.Stimulator.Status;
if ~isMP
    try
        initErr = 1;
        while initErr ~=0
            fprintf('Reinitializing stimulator...');
            startTime = tic;
            closeErr = PS_CloseAllStim();
            if closeErr == 0
                pause(1);
            end
            initErr = PS_InitAllStim();
            [endTime,unit] = getEndTime(startTime);
            fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
        end
    catch
    end

    % % Discharge mode
    % if ~isDischargeOn
    %     PS_SetAutoDischarge(1,0);
    % end
    

    % Set stimulation rate
    fprintf('\t');
    stimRate = File.Parameters.StimulationRate;
    isQuit = setAllStimRate(stimRate);
    if isQuit
        return;
    end
    % Set number of repetitions for all stimulators
    fprintf('\t');
    numOfPulses = File.Parameters.NumberOfPulses;
    isQuit = setAllNumOfPulses(numOfPulses);
    if isQuit
        return;
    end
    fprintf('\t');
    % Set the monitor channel
    if numOfChannelStim > 1
        channelNum = channelStim_arr(1);
    else
        channelNum = channelStim_arr;
    end
    isQuit = setMonitorChannel(File,channelNum);
    if isQuit
        return;
    end
    fprintf('\t');
end

%% Stimulation Channel(s)
fprintf('Setting stimulation pattern...\n');
startTime = tic;
if isArb || isAlwaysArb
    % isSet = false;
    % while ~isSet
        fprintf('\t\t');
        fileName_cell = getArbPattern(File,pattern,channelStim_arr);
        fprintf('\t\t');
        setArbPattern(plexoStim_arr,fileName_cell);
    % end
else
    setRectPattern(plexoStim_arr,pattern,2);
end
[endTime,unit] = getEndTime(startTime);
fprintf('\tTime Elapsed: %.2f %s\n',endTime,unit);

%% Zero Current
% zeroPattern = pattern;
zeroPattern = pattern;
zeroPattern.A1 = 0;
zeroPattern.A2 = 0;
if isTriphasic
    zeroPattern.A3 = 0;
    zeroPattern.W3 = phaseWidth3;
end
% if isAP
%     fprintf('\Making controlled current return pattern...');
%     patternType = 1;
%     zeroPattern.A1 = roundStim(amplitude2 / 15) * 1e3;
%     zeroPattern.A2 = roundStim(amplitude1  / 15) * 1e3;
%     fprintf('OK\n');
% else
if numOfZeroChannels > 0 && ~isCG
    fprintf('Setting zero current pattern...');
    if isArb || isAlwaysArb
        patternType = 1;
    else
        patternType = 0;
    end
    if patternType == 0  && ~isCG
        fprintf('OK\n');
    end
end

if numOfZeroChannels > 0 && ~isCG
    startTime = tic;
    switch patternType
        case 0
            rectType = 2;
        case 1
            fileNameZero_cell = getArbPattern(File,zeroPattern); %zeroChannel_arr
    end
    % isSet = false;
    % while ~isSet
        fprintf('\t');
        switch patternType
            case 0
                setRectPattern(zeroChannel_arr,zeroPattern,rectType);
            case 1
                setArbPattern(zeroChannel_arr,fileNameZero_cell,zeroPattern);
        end
    % end
    [endTime,unit] = getEndTime(startTime);
    fprintf('\tTime Elapsed: %.2f %s\n',endTime,unit);
% else
%     [endTime,unit] = getEndTime(startTime);
%     fprintf('NONE \t\t(%.2f %s)\n',endTime,unit);
end

% % Return Channel
% if numOfReturns > 0
% %     returnX = 0;
% %     returnY = 0;
%     for returnNum = 1:numOfReturns
%         channelReturn = plexonReturn_arr(returnNum);
% %         while ~isempty(returnX) || ~isempty(returnY)
%             PS_SetPatternType(1,channelReturn,0);
%             PS_SetPatternType(1,channelReturn,1);
% %             filename = makeArbitrary(File,[]);
% %             PS_LoadArbPattern(1,channelReturn,filename);
% %             returnX = PS_GetArbPatternPointsX(1,channelReturn);
% %             returnY = PS_GetArbPatternPointsY(1,channelReturn);
% %         end
%     end
% end

end