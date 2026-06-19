This README file describes the contents of the files included in the
2008-02-08 version of the proposed proposal for spot TV and spot cable 
media.  This version is intended as a 1.0 schema that should be used in 
conjunction with the business rules document. 

NOTE: The routing information formerly in the transmission section of every
document was moved to a separate envelope wrapper document maintained
by the AAAA.
 
NOTE: The third number in the version represents the number of times the
AAAA and TVB schemas have changed while the proposal schema has remained
the same.  

README-0.3.0.5A.txt - This document.

aaaaMessageHeader-0.2.0.1.xsd - This file contains a chameleon 
schema that is the base of the template document used to create the 
proposal documents.  

TVBGeneralTypes-0.0.1.xsd -This file contains the TVB library of  generic and
non-media related types.  This file was previously maintained by the AAAA.

spotTV-2.4.1.0.xsd - This file contains an updated TVB library of TVB elements 
and types.  

spotTVCableProposal-0.3.0.5A.xsd - This file is a  backwardly compatible patch
for version 0.3.0.5 of the proposal schema for spot TV and spot cable media. 
A leading space was erroneously included in the type definition for the outletId 
attribute, i.e,<xsd:attribute name="outletId" type=" proposal:outletIdType" use="required"/>.    
Some validators are able to handle the extra space by collapsing the white space 
when determining the type, however at least one validator,  e.g., Xerces-C++ Version 2.8.0, 
doesn't and raises a cannot resolve error in the schema. This patch supports validating 
proposal XML documents with either version 0.3.0.5 or 0.3.0.5A for backwards compatibility.  
Selling systems should continue to produce documents marked as 0.3.0.5 so that 
systems using 0.3.0.5  or 0.3.0.5A can receive the document.. This patch corrects schema 
use problems in the technical process and if implemented as suggested should not create
any issues between any partners regardless of whether or not they are working with the patch. 

DummySpotTVProposal-0.3.0.5A.xml - This file contains a sample spot TV 
proposal based on the spotTVCableProposal-0.3.0.5A.xsd schema.

DummySpotCableProposal-0.3.0.5A.xml - This file contains a sample spot cable 
proposal  based on the spotTVCableProposal-0.3.0.5A.xsd schema.
