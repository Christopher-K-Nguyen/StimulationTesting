%% Heading
% This script is based on StimDemo.m provided by Plexon. All MATLAB support
% files by Plexon should be in the Plexon Stimulator folder
% This script allows you to stimulate with a square wave pulse, and lets
% you specify the current amplitude, the width, and the frequency.

% Written by Chris Nguyen, Neural Interfaces Lab

clear; clc;
try
    delete(findall(groot));
catch
end
beep on;
fprintf('PROGRAM STARTED\n\n');
warning ('off','all');

%% Constants

%% Find functions folder
findFunctions();
fprintf('\n');

%% Create structure for database
% Initialize structure to store data collection
fprintf('Initalizing database...');
Mapping = struct( ...
    'Description','', ...
    'Channel',[], ...
    'Amplitude',[], ...
    'ChargePhase',[], ...
    'ChargeInjection',[]);

Configuration = struct( ...
    'ID','MP', ...
    'Type','monopolar', ...
    'Number',1);

Parameters = struct( ...
    'Device','', ...
    'Environment','', ...
    'Mapping', Mapping,...
    'SurfaceArea',[], ...
    'Configuration',Configuration, ...
    'WorkingElectrode',[],...
    'ReferenceElectrode',[], ...
    'CounterElectrode',[], ...
    'Amplitude1',[], ...
    'PhaseWidth1',[], ...
    'InterphaseDelay',[], ...
    'Amplitude2',[], ...
    'PhaseWidth2',[], ...
    'DischargeDelay',[], ...
    'StimulationRate',[], ...
    'NumberOfPulses',Inf, ...
    'Bias',[], ...
    'Polarity',[], ...
    'Symmetry',[], ...
    'PulseWidth',[], ...
    'Depolarization',[]);

Stimulator = struct(...
    'SerialNumber','',...
    'Firmware','',...
    'Description','',...
    'VoltageScaling',1,...  % V/V
    'CurrentScaling',1e-3,...  % mV/uA
    'DigitalDelay',1.5, ...    % us
    'Status',false);% 'InterpulseLow',true, ...

Video = struct( ...
    'Time',[], ...
    'Frame',[]);

Camera = struct( ...
    'Enable',false, ...
    'Object',[], ...
    'Mode',[], ...
    'Rate',[], ...
    'Interval',[], ...
    'Path',[], ...
    'Next',[], ...
    'Video',Video);

User = struct( ...
    'Name', ...
    'Email', ...
    'Phone', ...
    'Carrier');

File = struct( ...	% initialize structure
    'Notebook','', ...           % notebook
    'Subject','', ...            % name
    'ID','', ...
    'Parameters',Parameters, ...         % parameters
    'Test',[], ...
    'Stimulator',Stimulator, ...
    'Oscilloscope',[], ...
    'Camera',Camera, ...
    'Arduino',[], ...
    'Data',struct(), ...               % data
    'DateTimeCreated',getDateTime(), ...    % date and time created
    'DateTimeModified','', ...   % date and time completed
    'User',User, ...              % name
    'Path','');
fprintf('OK\n\n');   % structure initialized

%% Physical Setup Check
% Physical Setup
[File,isQuit] = getPhysSetup_Tek(File);
if isQuit
    fprintf('OK\n\n');
    return;
end
fprintf('\n');

%% Experiment Information
[File,isQuit] = setExperimentInfo(File);
if isQuit
    fprintf('OK\n\n');
    return;
end
fprintf('\n');

% Experiment
expType = File.Test.Experiment;
isPulsing = contains2(expType,{'SP','LP'});
isLongPulsing = contains2(expType,{'LP'});

% Stimulation Configuration
numOfTests = 1;
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isCG = contains2(configID,'CG');
isPBP = contains2(configID,'PBP');
isPTP = contains2(configID,'PTP');
isPartial = isPBP || isPTP;
channelGroup_mat = File.Test.Groups;
[numOfGroups,numOfElectrodes] = size(channelGroup_mat);
returnElectrode = File.Parameters.CounterElectrode.Type;
if isPulsing
    testChannel_arr = File.Parameters.Channels.Test;
    numOfGroups = length(testChannel_arr);
else
    % Stimulation Rate
    stimRate_arr = File.Parameters.StimulationRate;
    numOfStimRate = length(stimRate_arr);

    % Phase Width
    phaseWidth1_arr = File.Parameters.PhaseWidth1;
    phaseWidth2_arr = File.Parameters.PhaseWidth2;
    pulseWidth_arr = File.Parameters.PulseWidth;
    numOfPhaseWidth1 = length(phaseWidth1_arr);
    numOfPhaseWidth2 = length(phaseWidth2_arr);
    numOfPulseWidth = length(pulseWidth_arr);

    % Number of Tests
    subjectName = File.Subject;
    if numOfStimRate > 1
        numOfPlacesRate = length(num2str(max(stimRate_arr)));
        numOfTests = numOfStimRate;
    end
    if numOfPhaseWidth1 > 1
        numOfPlacesWidth1 = length(num2str(max(phaseWidth2_arr)));
        numOfTests = numOfPhaseWidth1;
    end
    if numOfPhaseWidth2 > 1
        numOfPlacesWidth2 = length(num2str(max(phaseWidth2_arr)));
        numOfTests = numOfPhaseWidth2;
    end
    if numOfPulseWidth > 1 && numOfPhaseWidth2 > 1
        numOfPlacesPulse = length(num2str(max(pulseWidth_arr)));
        numOfTests = numOfPlacesPulse;
    end
end

%% Reset USB Connections
% isQuit = resetUSB();
% if isQuit
%     fprintf('OK\n\n');
%     return;
% end

%% Initialize Stimulator
[File,isQuit] = initializeStimulator(File);
if isQuit
    fprintf('OK\n\n');
    return;
end

if File.Stimulator.Status
    %% Establish connection with oscilloscope
    % Connect to oscilloscope
    [File,isQuit] = setOscilloscope(File);
    if isQuit
        fprintf('OK\n\n');
        return;
    end
    fprintf('\n');

    fieldsList = vertcat(File.Oscilloscope(:).Fields);
    activeChannel_tf = containsi(fieldsList,{'act','work','pot'});
    diffChannel_tf = containsi(fieldsList,{'diff'});
    voltageChannel_tf = containsi(fieldsList,{'volt'});
    if any(activeChannel_tf)
        specialChannel_idx = find(activeChannel_tf);
    elseif any(diffChannel_tf)
        specialChannel_idx = find(diffChannel_tf);
    elseif any(voltageChannel_tf)
        specialChannel_idx = find(voltageChannel_tf);
    else
        specialChannel_idx = 1;
    end
    specialChannel = fieldsList(specialChannel_idx);
    isVoltageSpecial = contains2(specialChannel,'volt');
    hasReturnChannel = contains2(fieldsList,{'ret','count'});
    hasAltActive = isVoltageSpecial && hasReturnChannel;

    %% Initialize Data
    Status = struct( ...
        'Description','', ...
        'Good',true, ...
        'Normal',true, ...
        'PotentialLimit',0, ...
        'VoltageSafety',true, ...
        'VoltageCompliance',false, ...
        'MaxCurrent',false, ...
        'TooLong',false, ...
        'Precise',true, ...
        'Quit',false);

    VoltageValues = struct( ...
        'Time',zeros(1,6), ...
        'Voltage',zeros(1,6), ...
        'Plot',zeros(1,6));

    PotentialExcursion = struct( ...
        'Time',zeros(1,2), ...
        'Voltage',zeros(1,2));

    Measurement = struct( ...
        'VoltageValues',VoltageValues, ...
        'PotentialExcursion',PotentialExcursion);
    
    if isLongPulsing
        Capture = struct( ...
            'Index',[], ...
            'PulseNumber',[], ...
            'Time',[]);
    else
        Capture = struct( ...
            'Index',[], ...
            'Time',[]);
    end
    fieldsList = vertcat(File.Oscilloscope(:).Fields);
    numOfScopeChannels = length(fieldsList);
    for scopeChannelNum = 1:numOfScopeChannels
        field = fieldsList{scopeChannelNum};
        Capture.(field) = [];
    end
    if hasAltActive
        Capture.Active = [];
    end
    Capture.CurrentDensity = [];
    Capture.Amplitude = [];  % amplitude
    Capture.PhaseWidth = [];
    Capture.ChargePhase = [];   % charge phase
    Capture.ChargeInjection = [];        % charge injection
    Capture.PotentialExcursion = [];
    Capture.DrivingVoltage = [];
    Capture.EffectiveCapacitance = [];
    Capture.AccessVoltage = [];
    Capture.AccessResistance = [];
    Capture.DrivingPotential = [];
    Capture.ChargingCapacitance = [];
    Capture.Measurement = Measurement;
    Capture.CurrentChange = 0;
    Capture.DateTime = '';               % date and time completed
    Capture.Status = Status;

    Fitting = struct( ...
        'Fit1',[], ...
        'Fit2',[]);

    % initialize structure
    Data = struct( ...
        'Active','', ...
        'Return','', ...
        'Reference','', ...
        'ActiveChannel',[], ... % channel number
        'ReturnChannel',[], ...
        'Name','', ...
        'ID','', ...
        'SurfaceArea',[], ...
        'Capture',Capture, ...
        'Amplitude',[], ...
        'ChargePhase',[], ...
        'ChargeInjection',[], ...
        'PotentialExcursion',[], ...
        'DrivingVoltage',[], ...
        'EffectiveCapacitance',[], ...
        'AccessVoltage',[], ...
        'AccessResistance',[], ...
        'ChargingCapacitance',[], ...
        'Fitting',Fitting, ...
        'DateTime','', ...
        'TimeElapse',0);

    % Decalre Matrices
    % dataLength = File.Oscilloscope(1).Settings.DataLength;
    File.Data = Data;
    File.Data(1:numOfGroups) = Data;
    for groupNum = 1:numOfGroups
        activeChannelNum = channelGroup_mat(groupNum,1);
        channelName = sprintf('Channel %02d',activeChannelNum);
        channelID = sprintf('CH%02d',activeChannelNum);
        if ~isMP
            channelName = [channelName ' v ']; %#ok<*AGROW>
            channelID = [channelID '_v_'];
            if isCG
                channelName = [channelName 'CG'];
                channelID = [channelID 'CG'];
            else
                numOfReturns = numOfElectrodes - 1;
                return_arr = channelGroup_mat (groupNum,2:numOfElectrodes);
                return_cell = cell(1,numOfReturns);
                for idx = 1:numOfReturns
                    channelReturn = return_arr(idx);
                    return_cell{idx} = sprintf('%02d',channelReturn);
                end
                returnName_use = strjoin(return_cell,',');
                returnID_use = strjoin(return_cell,'_');
                channelName = [channelName returnName_use];
                channelID = [channelID returnID_use];
            end

            if isPartial
                channelName = [channelName ',' returnElectrode];
                channelID = [channelID '_' returnElectrode];
            end
        end

        File.Data(groupNum).Name = channelName;
        File.Data(groupNum).ID = channelID;
    end

    %% Initialize Arduino
    [File,isQuit] = setArduino(File);
    if isQuit
        fprintf('OK.\n\n');
        return;
    end

    %% Stimulation
    File_old = File;
    start_idx = 1;
    startTime = tic;
    switch expType
        case 'SP'
            [File_new,isQuit] = runShortPulsing(File);
        case 'LP'
            [File_new,isQuit] = runLongPulsing(File);
        case 'PS'
            [File_new,isQuit] = runProgressiveStress(File);
        case {'VT','TV'}
            if numOfTests == 1
                [File_new,isQuit] = runVoltageTransient(File);
            else
                if numOfStimRate > 1 && (numOfPhaseWidth1 > 1 || numOfPhaseWidth2 > 1)
                    for stimRate_idx = 1:numOfStimRate
                        stimRate = stimRate_arr(stimRate_idx);
                        File.Parameters.StimulationRate = stimRate;
                        stimRate_eval = sprintf("sprintf('%%0%dd',stimRate)",numOfPlacesRate);
                        stimRate_use = eval(stimRate_eval);
                        if numOfPhaseWidth1 > 1 && numOfPhaseWidth2 > 1
                            for width_idx = 1:numOfPulseWidth
                                phaseWidth1 = phaseWidth1_arr(width_idx);
                                phaseWidth2 = phaseWidth2_arr(width_idx);
                                pulseWidth = phaseWidth1_arr(width_idx);
                                File.Parameters.PhaseWidth1 = phaseWidth1;
                                File.Parameters.PhaseWidth2 = phaseWidth2;
                                File.Parameters.PulseWidth = pulseWidth;
                                pulseWidth_eval = sprintf("sprintf('%%0%dd',pulseWidth)",numOfPlacesPulse);
                                pulseWidth_use = eval(pulseWidth_eval);
                                subject_new = sprintf('%s_%spps_%sus',subjectName,stimRate_use,pulseWidth_use);
                                File.Subject = subject_new;
                                [File_new,isQuit] = runVoltageTransient(File);
                                if isQuit
                                    break;
                                end
                            end
                        elseif numOfPhaseWidth1 > 1 && numOfPhaseWidth2 == 1
                            for width_idx = 1:numOfPhaseWidth1
                                phaseWidth1 = phaseWidth1_arr(width_idx);
                                File.Parameters.PhaseWidth1 = phaseWidth1;
                                phaseWidth1_eval = sprintf("sprintf('%%0%dd',phaseWidth1)",numOfPlacesWidth1);
                                phaseWidth2_use = eval(phaseWidth1_eval);
                                subject_new = sprintf('%s_%spps_%sus',subjectName,stimRate_use,phaseWidth1_use);
                                File.Subject = subject_new;
                                [File_new,isQuit] = runVoltageTransient(File);
                                if isQuit
                                    break;
                                end
                            end
                        elseif numOfPhaseWidth2 > 1 && numOfPhaseWidth1 == 1
                            for width_idx = 1:numOfPhaseWidth2
                                phaseWidth2 = phaseWidth2_arr(width_idx);
                                File.Parameters.PhaseWidth2 = phaseWidth2;
                                phaseWidth2_eval = sprintf("sprintf('%%0%dd',phaseWidth2)",numOfPlacesWidth2);
                                phaseWidth2_use = eval(phaseWidth2_eval);
                                subject_new = sprintf('%s_%spps_%sus',subjectName,stimRate_use,phaseWidth2_use);
                                File.Subject = subject_new;
                                [File_new,isQuit] = runVoltageTransient(File);
                                if isQuit
                                    break;
                                end
                            end
                        end
                        newVarFile = getVarName(File_new);
                        if ~isQuit
                            notebook = File.Notebook;
                            subject = File.Subject;
                            test = File.Test.ID;
                            filename = ['File_' notebook '_' subject '_' test];
                            evalPrompt = sprintf('%s = %s;',filename,newVarFile);
                            eval(evalPrompt);
                        else
                            evalPrompt = sprintf('FailedFile = %s;',newVarFile);
                            eval(evalPrompt);
                            return;
                        end
                    end
                else
                    for test_idx = start_idx:numOfTests
                        if numOfStimRate > 1
                            stimRate = stimRate_arr(test_idx);
                            File.Parameters.StimulationRate = stimRate;
                            stimRate_eval = sprintf("sprintf('%%0%dd',stimRate)",numOfPlacesRate);
                            stimRate_use = eval(stimRate_eval);
                            subject_new = sprintf('%s_%spps',subjectName,stimRate_use);
                        elseif numOfPhaseWidth1 > 1 && numOfPhaseWidth2 > 1
                            phaseWidth1 = phaseWidth1_arr(width_idx);
                            phaseWidth2 = phaseWidth2_arr(width_idx);
                            pulseWidth = phaseWidth1_arr(width_idx);
                            File.Parameters.PhaseWidth1 = phaseWidth1;
                            File.Parameters.PhaseWidth2 = phaseWidth2;
                            File.Parameters.PulseWidth = pulseWidth;
                            pulseWidth_eval = sprintf("sprintf('%%0%dd',pulseWidth)",numOfPlacesPulse);
                            pulseWidth_use = eval(pulseWidth_eval);
                            subject_new = sprintf('%s_%spps_%sus',subjectName,stimRate_use,pulseWidth_use);
                        elseif numOfPhaseWidth1 > 1 && numOfPhaseWidth2 == 1
                            phaseWidth1 = phaseWidth1_arr(test_idx);
                            File.Parameters.PhaseWidth2 = phaseWidth2;
                            phaseWidth1_eval = sprintf("sprintf('%%0%dd',phaseWidth1)",numOfPlacesWidth1);
                            phaseWidth1_use = eval(phaseWidth1_eval);
                            subject_new = sprintf('%s_%sus',subjectName,phaseWidth1_use);
                        elseif numOfPhaseWidth2 > 1 && numOfPhaseWidth1 == 1
                            phaseWidth2 = phaseWidth2_arr(test_idx);
                            File.Parameters.PhaseWidth2 = phaseWidth2;
                            phaseWidth2_eval = sprintf("sprintf('%%0%dd',phaseWidth2)",numOfPlacesWidth2);
                            phaseWidth2_use = eval(phaseWidth2_eval);
                            subject_new = sprintf('%s_%sus',subjectName,phaseWidth2_use);
                        else
                            subject_new = subjectName;
                        end
                        File.Subject = subject_new;
                        [File_new,isQuit] = runVoltageTransient(File);
                        newVarFile = getVarName(File_new);
                        if ~isQuit
                            notebook = File.Notebook;
                            subject = File.Subject;
                            test = File.Test.ID;
                            filename = ['File_' notebook '_' subject '_' test];
                            evalPrompt = sprintf('%s = %s;',filename,newVarFile);
                            eval(evalPrompt);
                        else
                            evalPrompt = sprintf('FailedFile = %s;',newVarFile);
                            eval(evalPrompt);
                            return;
                        end
                    end
                end
            end      
    end
    if numOfTests == 1 && ~contains2(expType,{'SP'})
        newVarFile = getVarName(File_new);
        if ~isQuit
            notebook = File.Notebook;
            subject = File.Subject;
            test = File.Test.ID;
            filename = ['File_' notebook '_' subject '_' test];
            evalPrompt = sprintf('%s = %s;',filename,newVarFile);
            eval(evalPrompt);
        else
            evalPrompt = sprintf('FailedFile = %s;',newVarFile);
            eval(evalPrompt);
            return;
        end
    end

    % Stop
    [endTime,unit] = getEndTime(startTime);
    if ~isQuit
        fprintf('Experiment completed: %.2f %s\n\n',endTime,unit);
    else
        fprintf('Experiment failed: %.2f %s\n\n',endTime,unit);
    end

    %% End of program
    % closeStimulator(File);
    % close all;
    % close(findall(0,'type','figure'));
    beep;
    fprintf('PROGRAM ENDED\n\n');
end

%% Find Functions
function [] = findFunctions()
% Personal Functions
functionsCKN = 'Z:\2_ Projects and Data\0_ Personal Folders\Christopher Nguyen\MATLAB\CKN_Functions';
addpath(functionsCKN);

% Script Functions
functionFolder = 'PlexStimTek_Functions';                           % function folder name
functions_location_Cdrive = 'C:\PlexonSDKs\PlexStimTek_Functions';  % function folder in :C
fprintf('Searching for Function Folder in directory...');   % searching for SDK
if isfolder(functionFolder)              % functions found in directory
    fprintf('OK\n');                                     % functions found
    path(path,functionFolder);                              % adding functions to path (just in case)
else                                                            % functions not found
    prompt = 'FUNCTIONS NOT FOUND IN DIRECTORY';             	% message prompt
    waitfor(msgbox(prompt,'ERROR'));                         % functions not found in directory
    fprintf('NOT FOUND.\n');
    fprintf('Searching for Funtion Folder in :C...') ;          % searching for Function Folder in C:
    if exist(functions_location_Cdrive,'dir') 	% functions found
        fprintf('OK\n');                                     % functions found in C:
        path(path,functions_location_Cdrive);                	% adding Functions to directory
    else                                                      	% functions not found
        fprintf('NOT FOUND.\n');                                % functions not found in C:
        prompt1 = 'Find Funtion Folder to add to directory.';   % select folder into directory
        prompt2 = sprintf('"%s"',functionFolder);            	% folder to find
        disp(prompt1);                                      	% print message
        waitfor(msgbox({prompt1,prompt2},'ERROR'));       	% message box
        functions_location = uigetdir();                    	% input folder location
        cancel_functions_location = isempty(functions_location);% ccncel dialog
        if cancel_functions_location	% cancel detected
            fprintf('\nQuitting...\n');  	% quitting
            return;                       	% exit program
        else                                   	% path entered
            fprintf('\nFound'); % path located
            path(path,functions_location);    	% adding Funtion Folder to directory
        end
    end
end

% Find SDK directory
SDK_folder = 'MATLAB SDK for PlexStim 2.0 - 64 bit';                        % SDK folder name
SDK_location_Cdrive = 'C:\PlexonSDKs\MATLAB SDK for PlexStim 2.0 - 64 bit'; % SDK in :C
folderExists = 7;                                                           % folder existing value
fprintf('Searching for SDK in directory...');  % searching for SDK
if exist(SDK_folder,'dir') == folderExists  % SDK found in directory
    fprintf('OK\n');    	% SDK found
    path(path,SDK_folder);                  % adding SDK to path (just in case)
else                                                           	% SDK not found
    prompt = 'SDK NOT FOUND IN DIRECTORY';                  	% message prompt
    waitfor(msgbox(prompt,titleError));                         % SDK not found in directory
    fprintf('\nSearching for SDK in "%s"...\n',SDK_location_Cdrive);% searching for SDK in C:
    if exist(SDK_location_Cdrive,'dir') == folderExists         % SDK found
        fprintf('OK.\n');
        fprintf('Adding SDK location to directory.\n\n');              % SDK found in C:
        path(path,SDK_location_Cdrive);                         % adding SDK to directory
    else                                                % SDK not found
        fprintf('\nSDK NOT FOUND IN C:\n');                    % SDK not found in C:
        prompt1 = 'Find SDK folder to add to directory...';   % select folder into directory
        prompt2 = sprintf('"%s"',SDK_folder);               % folder to find
        fprintf('%s...',prompt1);                                      % print message
        waitfor(msgbox({prompt1,prompt2},titleError));	% message box
        SDK_location = uigetdir();                      % input folder location
        cancel_SDK_location = isempty(SDK_location);    % ccncel dialog
        if cancel_SDK_location == 1     % cancel detected
            fprintf('Quitting...\n\n'); % quitting
            return;                     % exit program
        else                            % path entered
            fprintf('OK\n');% path located
            path(path,SDK_location);	% adding SDK to directory
        end
    end
end

end