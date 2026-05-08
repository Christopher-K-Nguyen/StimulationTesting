function [File,quitProgram] = selectChannels(File)
%% Constants
% Buttons
BUTTON_NORM = 'Normal';
BUTTON_RATE = 'Rate';
BUTTON_MULTI = 'Multipolar';
BUTTON_EXP = 'EXP';
BUTTON_FIXED = 'FIXED';
BUTTON_MAX = 'MAX';
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX
% Values
CHARGE_CHECK = 1;
% NUM_OF_ANIMALS = 12;
% NUM_OF_CONTROLS = 8;
% NUM_OF_SUBJECTS = NUM_OF_ANIMALS + NUM_OF_CONTROLS;
BLACKROCK_OMNETICS = transpose(1:16);
% BLACKROCK_TO_PLEXON_OMNETICS = [9;10;11;12;13;14;15;16;1;2;3;4;5;6;7;8];
% BLACKROCK_TO_PLEXON_OMNETICS_NEW = [1;16;7;15;6;14;5;13;4;12;3;11;2;10;1;9];
BLACKROCK_TO_PLEXON_OMNETICS_NEW = BLACKROCK_OMNETICS;

%% Variables
confirmTestType = false;
confirmSubjectSelect = false;
confirmAplitude = false;
quitProgram = false;

%% Mapping
mappingDescritpion = 'From pad side, wire bundle at bottom';
channelMapping = [ ...
    13 14 15 16; ...
    9 10 11 12; ...
    5 6 7 8; ...
    1 2 3 4];
plexonMapping = zeros(4,4);
amplitudeMapping = zeros(4,4);
chargePhaseMapping = zeros(4,4);
chargeInjectionMapping = zeros(4,4);

%% Device Selection
BUTTON_SINGLE = 'Single';
BUTTON_MULTI = 'Multi';

opts.Default = BUTTON_MULTI;       % option dedault
confirmNumOfChannels = false;
while ~confirmNumOfChannels
    % Confirm number of channel selection
    % Format questions
    questNumOfChannels = questdlg( ...
        'Are you stimulating a single- or multi-channel device?', ...
        'Number of Channels', ...
        BUTTON_SINGLE,BUTTON_MULTI,opts);
    % Confirm subject selection
    % Format questions
    promptQuesNumofChannels = sprintf('Number of channels selected: {\\bf%s}-channel',questNumOfChannels);
    % Question box
    questChoice = questdlg(...                      % question dialog
        promptQuesNumofChannels,...                 % question prompts
        'Confirm Number of Channel Selection',...   % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questChoice                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmNumOfChannels = true;               % confirm info
            fprintf('%s-channel\n',questNumOfChannels);  % info confirmed
            numOfChannels = questNumOfChannels;
        case BUTTON_TRY                             % try again
            confirmNumOfChannels = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
    end
end
if quitProgram
    return;
end

%% Device Type
UTD = 'UTD';
BRN = 'Blackrock';
NNX = 'NeuroNexus';
PLX = 'Plexon';

deviceType = '';
switch numOfChannels
    case BUTTON_SINGLE
        deviceType = 'SING';
    case BUTTON_MULTI
        device_cell = {UTD,BRN,NNX,PLX};
        initialChoice = 1;
        confirmDeviceType = false;
        % Subject Select
        while confirmDeviceType == false
            fprintf('Enter device type selection...');
            % Confirm subject selection
            % Dialog box
            [deviceType_idx,deviceType_tf] = listdlg(...	% list dialog
                'PromptString','Select device type',... % list prompts
                'ListString',device_cell,...    % list
                'Name','Device Type Selection',...
                'InitialValue',initialChoice,...
                'SelectionMode','single');
        
            % Collect input
            if ~deviceType_tf           % cancel detected
                fprintf('\nQuitting...'); % quitting
                quitProgram = true;
                break;                     % exit program
            else
                initialChoice = deviceType_idx;
            end
            deviceChoice = device_cell{deviceType_idx};
        
            % Confirm subject selection
            % Format questions
            promptQuestDeviceType = sprintf('Device type selected: {\\bf%s}',deviceChoice);
            % Question box
            questListSubject = questdlg(...                      % question dialog
                promptQuestDeviceType,...                       % question prompts
                'Confirm Device Type Selection',...                        % question title
                BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
                opts);                                      % dialog options
            % Confirmation
            switch questListSubject                              % apply choice
                case BUTTON_CONFIRM                         % check confirmation
                    confirmDeviceType = true;               % confirm info
                    switch deviceChoice
                        case UTD
                            deviceType = 'UTD';
                        case BRN
                            deviceType = 'BRN';
                        case NNX
                            deviceType = 'NNX';
                        case PLX
                            deviceType = 'PLX';
                    end
                    fprintf('%s\n',deviceChoice);  % info confirmed
                case BUTTON_TRY                             % try again
                    confirmDeviceType = false;               % trying again
                    fprintf('\nTrying agin...\n\n');          % starting over
                case BUTTON_CANCEL                          % quit
                    fprintf('\nQuitting...');             % quitting
                    quitProgram = true;
                    break;                                 % exit program
                otherwise                                   % cancel
                    fprintf('\nQuitting...');             % quitting
                    quitProgram = true;
                    break;                                 % exit program
            end
        end
        if quitProgram
            return;
        end

end

%% Test Type
while ~confirmTestType
    fprintf('Select test type...');
    % Question box
    questTestType = questdlg(...
        'Select test type.',...
        'Test Type',...
        BUTTON_NORM,BUTTON_RATE,BUTTON_MULTI,...
        opts);

    choice = sprintf('Test type: {\\bf%s}',questTestType);
    questChoice = questdlg(...
        choice,...
        'Select Test Type',...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
        opts);

    % Confirmation
    switch questChoice                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmTestType = true;               % confirm info
            %             fprintf('OK\n');  % info confirmed
        case BUTTON_TRY                             % try again
            confirmTestType = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
    end
end
if quitProgram
    return;
end

% Test Type
switch questTestType                              % apply choice
    case BUTTON_NORM                         % check confirmation
        testType = 'NORM';
        fprintf('normal\n');  % info confirmed
    case BUTTON_RATE                             % try again
        testType = 'RATE';
        fprintf('rate\n');          % starting over
    case BUTTON_MULTI
        testType = 'MULTI';
        fprintf('multipolar\n');
    otherwise                                   % cancel
        fprintf('\nQuitting...');             % quitting
        quitProgram = true;
        return;                                 % exit program
end

%% Waveform

confirmWaveform = false;
while ~confirmWaveform
    phaseWidth1 = 200;     % first phase pulse width
        interphaseDelay = 100; % interphase delay
        phaseWidth2 = phaseWidth1;     % second phase pulse width
        
end


switch testType
    case 'NORM'
        phaseWidth1 = 200;     % first phase pulse width
        interphaseDelay = 100; % interphase delay
        phaseWidth2 = phaseWidth1;     % second phase pulse width
        stimRate = 50;        % stimulation rate
        while ~confirmAplitude
            fprintf('Select charge type...');
            % Question box
            questChargeType = questdlg(...
                'Select charge type',...
                'Charge Type',...
                BUTTON_MAX,BUTTON_EXP,BUTTON_FIXED,...
                opts);

            choice = sprintf('Charge type: {\\bf%s}',questChargeType);
            questChoice = questdlg(...
                choice,...
                'Charge Type',...
                BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
                opts);

            % Confirmation
            switch questChoice                              % apply choice
                case BUTTON_CONFIRM                         % check confirmation
                    confirmAplitude = true;               % confirm info
                    fprintf('%s\n',questChargeType);  % info confirmed
                case BUTTON_TRY                             % try again
                    confirmAplitude = false;               % trying again
                    fprintf('\nTrying agin...\n\n');          % starting over
                case BUTTON_CANCEL                          % quit
                    fprintf('\nQuitting...');             % quitting
                    quitProgram = true;
                    break;                                 % exit program
                otherwise                                   % cancel
                    fprintf('\nQuitting...');             % quitting
                    quitProgram = true;
                    break;                                 % exit program
            end
        end

        switch questChargeType
            case BUTTON_EXP
                type = 'EXP';
                amplitude = ones(4,1);
                charge00 = amplitude * 0;
                charge04 = amplitude * -20;
                charge10 = amplitude * -50;
                charge20 = amplitude * -100;
                switch subjectSelect
                    case {'SA01';'SC01';'PA01';'PA05';'PC01'}
                        amplitude1 = [charge00;charge04;charge10;charge20]; % first phase amplitude
                    case {'SA02';'SC02';'PA02';'PC02'}
                        amplitude1 = [charge20;charge00;charge04;charge10]; % first phase amplitude
                    case {'SA03';'SC03';'PA03';'PC03'}
                        amplitude1 = [charge10;charge20;charge00;charge04]; % first phase amplitude
                    case {'PA04'}
                        amplitude1 = [charge04;charge10;charge20;charge00]; % first phase amplitude
                end
                expAmplitude_arr = amplitude1;
                [expCharge_arr,~] = getCharge(expAmplitude_ar,phaseWidth1);
            case BUTTON_FIXED
                [targetCharge,type,quitProgram] = selectCharge();
%                 charge_C = charge * 1e-9;
%                 phaseWidth_s = 200e-6;
%                 amplitude_A = charge_C / phaseWidth_s;
                targetCharge_arr = ones(16,1) * targetCharge;
                targetAmplitude_arr = -targetCharge_arr / phaseWidth1 * 1e3;
                targetAmplitude_round = round(targetAmplitude_arr);
                amplitude1 = targetAmplitude_round;

            case BUTTON_MAX
                type = 'MAX';
                amplitude = CHARGE_CHECK / phaseWidth1 * 1e3;
                amplitude_round = round(amplitude);
                amplitude1 = ones(16,1) * -amplitude_round;
        end
        amplitude2 = -amplitude1; % second phase amplitude
    case {'RATE','MULTI'}
        type = testType;
        [stimRate,quitProgram] = getStimRateParam(); % stimulation rate
        phaseWidth1 = 25;     % first phase pulse width
        interphaseDelay = 15; % interphase delay
        phaseWidth2 = phaseWidth1;     % second phase pulse width
        if contains2(type,'MULTI')
            [targetCharge,~,quitProgram] = selectCharge();
            targetCharge_arr = ones(16,1) * targetCharge;
            targetAmplitude_arr = -targetCharge_arr / phaseWidth1 * 1e3;
            targetAmplitude_round = round(targetAmplitude_arr );
            amplitude1 = targetAmplitude_round;
        else
            amplitude = 5;
            amplitude_round = round(amplitude);
            amplitude1 = ones(16,1) * -amplitude_round;
            amplitude2 = -amplitude1;
        end
        if quitProgram
            return;
        end
end
if quitProgram
    return;
end

% Multipolar
isMultiTest = contains2(type,'MULTI');
if isMultiTest
    folderpath = fullfile(SAVE_PATH,'AnimalStudy',subjectSelect);
    confirmFile = false;
    while ~confirmFile
        fprintf('Select previous MAT file...');
        [name,path] = uigetfile('*.mat','Previous MAT File',folderpath);
        filepath = fullfile(path,name);
        choice = sprintf('Selected File: {\\bf%s}',name);
        questChoice = questdlg(...
            choice,...
            'MAT File',...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,...
            opts);

        % Confirmation
        switch questChoice                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmFile = true;               % confirm info
                PreviousFile = load(filepath);
                close all;
                amplitude_arr = PreviousFile.Parameters.Amplitude1;
                for channelNum = 1:16
                    amplitude = amplitude_arr(channelNum);
                    channel_idx = find(channelMapping == channelNum,1);
                    plexonMapping(channel_idx) = plexonOmnetics(channelNum);
                    amplitudeMapping(channel_idx) = amplitude;
                    if amplitude == 0
                        channelMapping(channel_idx) = 0;
                    end
                end
                File.Parameters.Mapping = channelMapping;
                fprintf('"%s"\n',name);
                %             fprintf('OK\n');  % info confirmed
            case BUTTON_TRY                             % try again
                confirmFile = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                break;                                 % exit program
        end
    end
    if quitProgram
        return;
    end

    confirmMulti = false;
    numOfPoles_raw = '2';
    external_raw = '';
    while ~confirmMulti
        fprintf('Enter multipolar parameters...');
        promptMulti = { ...
            'Number of poles ({\bf0 for common ground, 2 for bipolar, 3 for tripolar, etc}):', ...
            'External return electrode ({\bfOptional}):'};
        defaultMulti = {numOfPoles_raw,external_raw};
        inputMulti = inputdlg( ...
            promptMulti, ...
            'Multipolar Parameters', ....
            [1 75],...
            defaultMulti, ...
            opts);
        if isempty(inputMulti)
            break;
        end
        numOfPols_raw = inputMulti{1};
        numOfPoles = str2double(numOfPols_raw);
        external_raw = inputMulti{2};
        isPt = contains2(external_raw,'Pt') && ~contains2(external_raw,'Ir');
        isPtIr = contains2(external_raw,'Pt') && contains2(external_raw,'Ir');
        isSS = contains2(external_raw,{'SS','stain','steel'});
        if isPt
            external = 'Pt';
        elseif isPtIr
            external = 'PtIr';
        elseif isSS
            external = 'SS';
        end
        numOfPoles_use = sprintf('Number of poles: {\\bd%s}',numOfPoles);
        external_use = sprintf('External electrode: {\\bf%s}',external);
        questMulti = {numOfPoles_use,external_use};
        questChoice = questdlg( ...
            questMulti, ...
            'Multipolar Parameters', ...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_QUIT, ...
            opts);
        % Confirmation
        switch questChoice                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmMulti = true;               % confirm info
                fprintf('OK\n');
            case BUTTON_TRY                             % try again
                confirmMulti = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                quitProgram = true;
                break;                                 % exit program
        end
    end
    if quitProgram
        return;
    end
else
    numOfPoles = 1;
    external = 'PtIr';
end

% Store
device = 'Blackrock';
File.Subject = subjectSelect;	% subject
isAnimal = contains2(subjectSelect,'A');
File.Parameters.Device = device;
File.Parameters.NumberOfChannels = 16;
File.Parameters.Mapping.Description = mappingDescritpion;
File.Parameters.Mapping.Channel = channelMapping;
File.Parameters.Mapping.Plexon = plexonMapping;
File.Parameters.Mapping.Amplitude = amplitudeMapping;
File.Parameters.Mapping.ChargePhase = chargePhaseMapping;
File.Parameters.Mapping.ChargeInjection = chargeInjectionMapping;
File.Parameters.Channels.(device) = BLACKROCK_OMNETICS;
if isAnimal
    plexonOmnetics = BLACKROCK_TO_PLEXON_OMNETICS_NEW;
else
    plexonOmnetics = BLACKROCK_OMNETICS;
end
File.Parameters.Channels.Plexon = plexonOmnetics;
File.Parameters.SurfaceArea = 5e3 * ones(16,1);
File.Parameters.Type = type;
File.Parameters.NumberOfPoles = numOfPoles;
File.Parameters.External = external;
File.Parameters.Amplitude1 = amplitude1;
File.Parameters.PhaseWidth1 = phaseWidth1;
File.Parameters.InterphaseDelay = interphaseDelay;
File.Parameters.Amplitude2 = amplitude2;
File.Parameters.PhaseWidth2 = phaseWidth2;
File.Parameters.StimulationRate = stimRate;
% isMultiTest = contains2(type,'MULTI');

isRateTest = contains2(type,'RATE');
isTargetMax = contains2(type,'MAX') || isRateTest;
if isTargetMax
    File.Parameters.Target.Charge = Inf;
    File.Parameters.Target.Amplitude = Inf;
    File.Parameters.Target.StepSize = [];
else
    if contains2(type,'nC')
        type = 'FIXED';
    end
    switch type
        case {'FIXED','MULTI'}
            charge = targetCharge_arr;
            amplitude = targetAmplitude_arr;
            stepSize = [];
        case 'EXP'
            charge = expCharge_arr;
            amplitude = expAmplitude_arr;
            stepSize = [];
    end
    File.Parameters.Target.Charge = charge;
    File.Parameters.Target.Amplitude = amplitude;
    File.Parameters.Target.StepSize = stepSize;
end

% Serial Number
len = length(subjectSelect);
switch len
    case 4
        if isAnimal
            serial_start = animalSerial_start;
        else
            serial_start = controlSerial_start;
        end
    case 3
        if isAnimal
            serial_start = animalSerial_start;
        else
            serial_start = controlSerial_start;
        end
end
serial_end = serialList(subjectSelect_idx);
serial = sprintf('%d-%06d',serial_start,serial_end);
File.SerialNumber = serial;
% fprintf('SN%s\n',serial);  % serial

end